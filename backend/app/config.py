"""Application configuration. All secrets come from environment variables / .env (server side only)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # repository root


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (ROOT / path)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_prefix="DAR_", extra="ignore")

    # LLM
    llm_provider: str = "openai_compatible"  # openai_compatible | anthropic
    llm_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    llm_api_key: str | None = None
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = "https://api.anthropic.com"
    generation_model: str = "Qwen/Qwen3-235B-A22B-Instruct-2507"
    eval_model: str = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
    llm_server_fallbacks: bool = True
    llm_timeout_s: float = 180.0
    # Optional USD prices per million tokens for cost estimates (OpenAI-compatible providers)
    llm_price_in_per_mtok: float | None = None
    llm_price_out_per_mtok: float | None = None

    # Storage
    database_url: str = "sqlite:///storage/dar.db"
    storage_dir: str = "storage"
    employer_registry: str = "config/employers.yaml"

    # Providers
    provider_timeout_s: float = 20.0
    provider_concurrency: int = 6
    provider_max_retries: int = 3
    provider_cache_ttl_s: int = 1800

    # Agent budgets
    agent_max_tool_calls: int = 30
    agent_max_llm_calls: int = 14
    agent_max_boards: int = 30
    agent_timeout_s: int = 240
    agent_token_budget: int = 400_000
    max_application_attempts: int = 3

    # Modes
    demo_mode: bool = False
    eval_mode: bool = False
    worker_enabled: bool = True
    worker_poll_s: float = 1.5
    upload_max_bytes: int = 5_000_000
    data_retention_days: int = 90

    @property
    def llm_available(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.llm_api_key)

    @property
    def storage_path(self) -> Path:
        return _resolve(self.storage_dir)

    @property
    def registry_path(self) -> Path:
        return _resolve(self.employer_registry)

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url.startswith("sqlite:///") and not self.database_url.startswith("sqlite:////"):
            rel = self.database_url[len("sqlite:///"):]
            if rel and rel != ":memory:":
                return "sqlite:///" + str(_resolve(rel))
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
