"""Application settings, loaded from environment variables (see .env.example).

Secrets are ``SecretStr`` so they never appear in ``repr()``/logs by accident.
"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- runtime ---
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    demo_mode: bool = False
    public_web_url: str = "http://localhost:3000"

    # --- database ---
    database_url: str = "postgresql+psycopg://commitmentos:commitmentos@localhost:5433/commitmentos"

    # --- auth ---
    jwt_secret: SecretStr = SecretStr("")
    jwt_ttl_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)
    cookie_name: str = "cos_session"
    cookie_secure: bool = False
    allow_registration: bool = True
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    action_token_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)

    # --- n8n <-> API (two distinct shared secrets, one per direction) ---
    n8n_inbound_secret: SecretStr = SecretStr("")  # n8n -> API   (header X-Webhook-Secret)
    n8n_outbound_secret: SecretStr = SecretStr("")  # API -> n8n   (n8n webhook header auth)
    n8n_base_url: str = "http://n8n:5678"
    n8n_timeout_seconds: float = Field(default=8.0, gt=0)
    n8n_max_attempts: int = Field(default=3, ge=1, le=6)

    # --- LLM (extraction only; never used for deterministic decisions) ---
    llm_provider: Literal["gemini", "openai_compat", "none"] = "gemini"
    # Google Gemini (native REST, header auth)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    # 0 = no hidden reasoning tokens (faster, cheaper); blank = send no thinkingConfig at all (some models reject it)
    gemini_thinking_budget: int | None = Field(default=0, ge=0, le=24576)
    # Any OpenAI-compatible /chat/completions server (Ollama, vLLM, LM Studio, OpenAI, ...)
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: SecretStr = SecretStr("")
    # Shared limits
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_transient_retries: int = Field(default=2, ge=0, le=5)
    llm_max_repair_attempts: int = Field(default=1, ge=0, le=2)
    llm_max_input_chars: int = Field(default=6000, ge=500)
    llm_max_output_tokens: int = Field(default=2000, ge=256, le=16000)

    # --- detection policy (deterministic thresholds applied AFTER validation) ---
    confidence_high: float = Field(default=0.85, gt=0, le=1)
    confidence_medium: float = Field(default=0.60, gt=0, lt=1)

    # --- reminder policy (deterministic) ---
    reminder_offsets_hours: Annotated[list[int], NoDecode] = [24, 6]
    escalate_after_hours: int = Field(default=24, ge=1)
    default_snooze_hours: int = Field(default=4, ge=1)
    notification_max_attempts: int = Field(default=3, ge=1, le=10)
    notification_retry_backoff_seconds: Annotated[list[int], NoDecode] = [60, 300, 1800]
    sending_lease_seconds: int = Field(default=300, ge=30)
    approval_ttl_hours: int = Field(default=48, ge=1)

    # --- time ---
    default_timezone: str = "UTC"
    business_day_end: str = "17:00"
    default_date_order: Literal["MDY", "DMY"] = "MDY"

    # --- notification identity ---
    notify_from_email: str = "commitmentos@localhost"
    calendar_provider: Literal["local", "google"] = "local"

    # --- rate limiting (in-memory, per process) ---
    rate_limit_login_per_minute: int = Field(default=10, ge=1)
    rate_limit_register_per_hour: int = Field(default=10, ge=1)
    rate_limit_api_per_minute: int = Field(default=600, ge=10)

    @field_validator("cors_origins", "reminder_offsets_hours", "notification_retry_backoff_seconds", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("gemini_thinking_budget", mode="before")
    @classmethod
    def _blank_means_model_default(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("reminder_offsets_hours")
    @classmethod
    def _offsets_positive_desc(cls, value: list[int]) -> list[int]:
        if not value or any(v <= 0 for v in value):
            raise ValueError("REMINDER_OFFSETS_HOURS must be a non-empty list of positive integers")
        return sorted(set(value), reverse=True)

    @field_validator("default_timezone")
    @classmethod
    def _tz_valid(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value

    @field_validator("business_day_end")
    @classmethod
    def _bde_valid(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> Settings:
        if not self.confidence_medium < self.confidence_high:
            raise ValueError("CONFIDENCE_MEDIUM must be lower than CONFIDENCE_HIGH")
        return self

    @property
    def business_day_end_time(self) -> time:
        return time.fromisoformat(self.business_day_end)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def assert_production_safe(self) -> None:
        """Refuse to boot in production with missing/weak secrets or insecure cookies."""
        problems: list[str] = []
        if len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        for name in ("n8n_inbound_secret", "n8n_outbound_secret"):
            if len(getattr(self, name).get_secret_value()) < 24:
                problems.append(f"{name.upper()} must be at least 24 characters")
        if self.is_production and not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.is_production and self.demo_mode:
            problems.append("DEMO_MODE must be false in production")
        if problems:
            raise RuntimeError("Insecure configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
