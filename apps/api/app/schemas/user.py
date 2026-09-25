from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.security import MIN_PASSWORD_LENGTH

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _valid_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown IANA timezone: {value!r}") from exc
    return value


class UserPreferences(BaseModel):
    """Per-user overrides of the global reminder/notification policy. ``None`` = use the default."""

    model_config = ConfigDict(extra="forbid")

    business_day_end: str | None = Field(default=None, description="HH:MM, what 'EOD'/'COB' means")
    date_order: Literal["MDY", "DMY"] | None = None
    reminder_offsets_hours: list[int] | None = Field(default=None, max_length=5)
    escalate_after_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    notify_email: bool = True
    notify_telegram: bool = False
    telegram_chat_id: str | None = Field(default=None, max_length=64)

    @field_validator("business_day_end")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        if v is not None and not _HHMM.match(v):
            raise ValueError("must be HH:MM (24h)")
        return v

    @field_validator("reminder_offsets_hours")
    @classmethod
    def _offsets(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v or any(not 1 <= h <= 24 * 30 for h in v):
            raise ValueError("offsets must be 1..720 hours")
        return sorted(set(v), reverse=True)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    display_name: str = Field(default="", max_length=120)
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _valid_tz(v)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    preferences: UserPreferences | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return _valid_tz(v) if v is not None else v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    timezone: str
    preferences: UserPreferences
    created_at: datetime
