"""Provider-configurable LLM layer.

Providers:
- ``openai_compatible`` (default): any OpenAI-compatible endpoint, configured for Nebius Token Factory.
- ``anthropic``: Anthropic Messages API.

Guarantees:
- Structured outputs are validated against Pydantic schemas; invalid output is retried once, then raises.
- Every call records usage into an optional UsageMeter (agent budgets, eval cost reporting).
- Missing credentials raise LLMUnavailable; callers never receive fabricated output.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import get_settings

log = logging.getLogger("dar.llm")
T = TypeVar("T", bound=BaseModel)

ANTHROPIC_PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


class LLMUnavailable(RuntimeError):
    """No credential configured - the feature is blocked, not faked."""


class LLMRefusal(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    pass


def _prices(model: str) -> tuple[float, float] | None:
    s = get_settings()
    if s.llm_provider == "anthropic":
        return ANTHROPIC_PRICES.get(model)
    if s.llm_price_in_per_mtok is not None and s.llm_price_out_per_mtok is not None:
        return (s.llm_price_in_per_mtok, s.llm_price_out_per_mtok)
    return None


@dataclass
class UsageMeter:
    max_calls: int | None = None
    max_tokens: int | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0
    latencies_ms: list[int] = field(default_factory=list)
    models: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget reached ({self.max_calls})")
        if self.max_tokens is not None and self.input_tokens + self.output_tokens >= self.max_tokens:
            raise BudgetExceeded(f"token budget reached ({self.max_tokens})")

    def record(self, model: str, input_tokens: int, output_tokens: int, latency_ms: int) -> None:
        with self.lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.latencies_ms.append(latency_ms)
            self.models.add(model)
            p = _prices(model)
            if p is None:
                self.cost_usd = None
            elif self.cost_usd is not None:
                self.cost_usd += input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1]


_clients: dict[str, Any] = {}
_lock = threading.Lock()


def provider_label() -> str:
    s = get_settings()
    return s.llm_provider


def _client():
    s = get_settings()
    if not s.llm_available:
        raise LLMUnavailable("No LLM API key configured (DAR_LLM_API_KEY); AI features are blocked.")
    with _lock:
        key = s.llm_provider
        if key not in _clients:
            if s.llm_provider == "anthropic":
                import anthropic

                _clients[key] = anthropic.Anthropic(
                    api_key=s.anthropic_api_key, base_url=s.anthropic_base_url, timeout=s.llm_timeout_s, max_retries=2
                )
            else:
                import openai

                _clients[key] = openai.OpenAI(
                    api_key=s.llm_api_key, base_url=s.llm_base_url, timeout=s.llm_timeout_s, max_retries=2
                )
        return _clients[key]


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _extract_json(text: str) -> Any:
    t = _THINK.sub("", text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start > 0:
        t = t[start:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # trim trailing prose after the JSON value
        end = max(t.rfind("}"), t.rfind("]"))
        return json.loads(t[: end + 1])


def _schema_for(schema: type[BaseModel]) -> dict:
    return schema.model_json_schema()


# ---------------------------------------------------------------------------------------- parse()
def parse(
    schema: type[T],
    *,
    system: str,
    user: str,
    model: str | None = None,
    effort: str = "medium",
    max_tokens: int = 8000,
    meter: UsageMeter | None = None,
    temperature: float = 0.2,
) -> tuple[T, dict]:
    """Single structured-output call, validated against ``schema``. Returns (object, metadata)."""
    s = get_settings()
    model = model or s.generation_model
    if meter:
        meter.check()
    if s.llm_provider == "anthropic":
        return _parse_anthropic(schema, system, user, model, effort, max_tokens, meter)
    return _parse_openai(schema, system, user, model, max_tokens, meter, temperature)


def _parse_openai(schema, system, user, model, max_tokens, meter, temperature):
    client = _client()
    js = _schema_for(schema)
    sys_prompt = (
        system
        + "\n\nRespond with a single JSON object only (no prose, no markdown) that validates against this JSON Schema:\n"
        + json.dumps(js)
    )
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
    last_err: Exception | None = None
    use_schema_format = True
    for attempt in range(3):
        t0 = time.monotonic()
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        if use_schema_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": js},
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # provider may not support json_schema for this model
            if use_schema_format and getattr(e, "status_code", None) in (400, 422):
                use_schema_format = False
                last_err = e
                continue
            raise
        latency = int((time.monotonic() - t0) * 1000)
        u = resp.usage
        if meter:
            meter.record(model, int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0), latency)
        choice = resp.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            last_err = LLMOutputError("model output truncated (max_tokens)")
            if attempt == 0:  # one retry asking for a tighter answer
                messages = messages + [{"role": "user", "content": "Your previous answer was too long and got cut off. "
                                        "Return the same JSON object again but much more concisely."}]
                continue
            break
        try:
            obj = schema.model_validate(_extract_json(content))
            return obj, {
                "model": model,
                "latency_ms": latency,
                "usage": {"input_tokens": getattr(u, "prompt_tokens", None), "output_tokens": getattr(u, "completion_tokens", None)},
                "attempts": attempt + 1,
            }
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            last_err = e
            messages = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": f"That output did not validate: {str(e)[:800]}. Return only the corrected JSON object."},
            ]
    raise LLMOutputError(f"structured output failed validation: {last_err}")


def _parse_anthropic(schema, system, user, model, effort, max_tokens, meter):
    client = _client()
    t0 = time.monotonic()
    extra = {}
    if get_settings().llm_server_fallbacks and model in {"claude-opus-5-5", "claude-sonnet-5-5"}:
        extra = {"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"}, "extra_body": {"fallbacks": "default"}}
    resp = client.messages.parse(
        model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
        output_format=schema, output_config={"effort": effort}, **extra,
    )
    latency = int((time.monotonic() - t0) * 1000)
    if meter:
        meter.record(model, int(resp.usage.input_tokens or 0), int(resp.usage.output_tokens or 0), latency)
    if resp.stop_reason == "refusal":
        raise LLMRefusal("model declined the request")
    if resp.parsed_output is None:
        raise LLMOutputError("no structured output")
    return resp.parsed_output, {"model": model, "latency_ms": latency,
                                "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}}


# ---------------------------------------------------------------------------------- chat_tools()
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    parse_error: str | None = None


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall]
    message: dict  # provider-neutral (OpenAI chat format) assistant message to append to history
    finish_reason: str | None


def chat_tools(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    meter: UsageMeter | None = None,
    max_tokens: int = 4000,
) -> AssistantTurn:
    """One step of a tool-using conversation.

    ``messages`` use OpenAI chat format (user / assistant(tool_calls) / tool). ``tools`` are
    ``{"name", "description", "parameters"}`` JSON-schema definitions.
    """
    s = get_settings()
    model = model or s.generation_model
    if meter:
        meter.check()
    if s.llm_provider == "anthropic":
        return _chat_tools_anthropic(system, messages, tools, model, meter, max_tokens)
    client = _client()
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}] + messages,
        tools=[{"type": "function", "function": t} for t in tools],
        tool_choice="auto",
        max_tokens=max_tokens,
        temperature=0.2,
    )
    latency = int((time.monotonic() - t0) * 1000)
    u = resp.usage
    if meter:
        meter.record(model, int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0), latency)
    msg = resp.choices[0].message
    calls: list[ToolCall] = []
    raw_calls = []
    for tc in msg.tool_calls or []:
        args, err = None, None
        try:
            args = json.loads(tc.function.arguments or "{}")
            if not isinstance(args, dict):
                args, err = None, "arguments must be a JSON object"
        except json.JSONDecodeError as e:
            err = f"invalid JSON arguments: {e}"
        calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args, parse_error=err))
        raw_calls.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}})
    text = _THINK.sub("", msg.content or "").strip()
    out_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if raw_calls:
        out_msg["tool_calls"] = raw_calls
    return AssistantTurn(text=text, tool_calls=calls, message=out_msg, finish_reason=resp.choices[0].finish_reason)


def _chat_tools_anthropic(system, messages, tools, model, meter, max_tokens) -> AssistantTurn:
    """Convert OpenAI-format history to Anthropic Messages format for one step."""
    client = _client()
    conv: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            conv.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                               "input": json.loads(tc["function"]["arguments"] or "{}")})
            conv.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                conv[-1]["content"].append(block)
            else:
                conv.append({"role": "user", "content": [block]})
    t0 = time.monotonic()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=conv,
        tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
    )
    if meter:
        meter.record(model, int(resp.usage.input_tokens or 0), int(resp.usage.output_tokens or 0), int((time.monotonic() - t0) * 1000))
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    calls, raw = [], []
    for b in resp.content:
        if b.type == "tool_use":
            calls.append(ToolCall(id=b.id, name=b.name, arguments=dict(b.input)))
            raw.append({"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}})
    out = {"role": "assistant", "content": text or None}
    if raw:
        out["tool_calls"] = raw
    return AssistantTurn(text=text, tool_calls=calls, message=out, finish_reason=resp.stop_reason)
