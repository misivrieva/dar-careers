"""Common provider interface, resilient HTTP retrieval and normalised vacancy schema."""
from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..config import get_settings

log = logging.getLogger("dar.providers")
USER_AGENT = "DAR-Careers/0.1 (job discovery; public job board API)"


class NormalisedJob(BaseModel):
    external_id: str
    provider: str
    employer_id: str
    employer_name: str
    title: str
    description_text: str | None = None
    description_html: str | None = None
    location_raw: str | None = None
    country: str | None = None
    city: str | None = None
    workplace_type: str | None = None
    remote_eligibility: str | None = None
    office_days_per_week: float | None = None
    employment_type: str | None = None
    department: str | None = None
    seniority: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_disclosed: bool = False
    published_at: datetime | None = None
    updated_at: datetime | None = None
    source_url: str | None = None
    application_url: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    on_call_required: bool | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    extraction_confidence: float = 0.5
    field_provenance: dict[str, str] = Field(default_factory=dict)
    is_synthetic: bool = False

    def content_hash(self) -> str:
        blob = json.dumps(
            [self.title, self.location_raw, self.description_text, self.salary_min, self.salary_max, self.employment_type],
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()


class FetchResult(BaseModel):
    employer_id: str
    ok: bool
    jobs: list[NormalisedJob] = Field(default_factory=list)
    error: str | None = None
    http_status: int | None = None
    from_cache: bool = False
    raw_hash: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    skipped_unlisted: int = 0
    malformed: int = 0


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        return None


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    ra = resp.headers.get("retry-after")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(ra)
            return max(0.0, (dt - datetime.now(dt.tzinfo)).total_seconds())
        except Exception:
            return None


async def get_json(client: httpx.AsyncClient, url: str, *, max_retries: int | None = None, base_delay: float = 0.5,
                   max_delay: float = 20.0) -> Any:
    """GET with timeouts, bounded retries, exponential backoff and Retry-After support."""
    s = get_settings()
    retries = s.provider_max_retries if max_retries is None else max_retries
    attempt = 0
    while True:
        try:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt >= retries:
                raise ProviderError(f"network error after {attempt + 1} attempts: {type(e).__name__}") from e
            await asyncio.sleep(min(max_delay, base_delay * 2**attempt))
            attempt += 1
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt >= retries:
                raise ProviderError(f"HTTP {resp.status_code} after {attempt + 1} attempts", resp.status_code)
            wait = _retry_after_seconds(resp) if resp.status_code == 429 else None
            await asyncio.sleep(min(max_delay, wait if wait is not None else base_delay * 2**attempt))
            attempt += 1
            continue
        if resp.status_code == 404:
            raise ProviderError("board not found (HTTP 404)", 404)
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}", resp.status_code)
        try:
            return resp.json()
        except ValueError as e:
            raise ProviderError("malformed response (invalid JSON)", resp.status_code) from e


class JobProvider(ABC):
    name: str

    @abstractmethod
    async def fetch_board(self, client: httpx.AsyncClient, employer: dict) -> FetchResult:
        """Fetch and normalise all published vacancies for one employer board."""

    async def fetch_job(self, client: httpx.AsyncClient, employer: dict, external_id: str) -> NormalisedJob | None:
        return None


def raw_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def monotonic_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
