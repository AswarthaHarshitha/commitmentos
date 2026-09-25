from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.enums import (
    DuePrecision,
    ObligationStatus,
    ObligationType,
    Priority,
    Recurrence,
    SourceDisposition,
    SourceType,
)


class DueInput(BaseModel):
    """A deadline expressed the way a person does: a local calendar date, optionally a local time.

    The server interprets it in the user's timezone (DST-safe). Omit ``time`` for a date-only
    deadline (due at the end of that local day)."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date
    time: dt.time | None = None


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset (e.g. 2026-09-25T17:00:00Z); use `due` for local date/time")
    return value


class _DueMixin(BaseModel):
    due_at: dt.datetime | None = Field(default=None, description="Absolute instant, must include a UTC offset")
    due: DueInput | None = None

    @field_validator("due_at")
    @classmethod
    def _due_at_aware(cls, v: dt.datetime | None) -> dt.datetime | None:
        return _aware(v)

    @model_validator(mode="after")
    def _one_due_form(self) -> _DueMixin:
        if self.due_at is not None and self.due is not None:
            raise ValueError("send either `due_at` or `due`, not both")
        return self


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ObligationCreate(_DueMixin):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    action: str | None = Field(default=None, max_length=500)
    obligation_type: ObligationType = ObligationType.TASK
    priority: Priority = Priority.MEDIUM
    recurrence: Recurrence | None = None
    requires_confirmation: bool = False
    counterparty_name: str | None = Field(default=None, max_length=200)
    counterparty_email: EmailStr | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v

    _strip = field_validator("description", "action", "counterparty_name")(_clean)


class ObligationPatch(_DueMixin):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    action: str | None = Field(default=None, max_length=500)
    obligation_type: ObligationType | None = None
    priority: Priority | None = None
    recurrence: Recurrence | None = None
    clear_recurrence: bool = False
    clear_due: bool = False
    requires_confirmation: bool | None = None
    counterparty_name: str | None = Field(default=None, max_length=200)
    counterparty_email: EmailStr | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("title must not be blank")
        return v.strip() if v else v

    @model_validator(mode="after")
    def _clear_vs_set(self) -> ObligationPatch:
        if self.clear_due and (self.due_at is not None or self.due is not None):
            raise ValueError("cannot both clear and set the deadline")
        return self


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hours: int | None = Field(default=None, ge=1, le=24 * 30)
    until: dt.datetime | None = None

    @field_validator("until")
    @classmethod
    def _until_aware(cls, v: dt.datetime | None) -> dt.datetime | None:
        return _aware(v)

    @model_validator(mode="after")
    def _exactly_one(self) -> SnoozeRequest:
        if self.hours is not None and self.until is not None:
            raise ValueError("send either `hours` or `until`, not both")
        return self


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class ScheduleRequest(BaseModel):
    """Ask n8n to put time on the calendar for this obligation (the click *is* the approval)."""

    model_config = ConfigDict(extra="forbid")

    start_at: dt.datetime | None = None
    duration_minutes: int = Field(default=30, ge=5, le=24 * 60)
    title: str | None = Field(default=None, max_length=300)

    @field_validator("start_at")
    @classmethod
    def _aware_start(cls, v: dt.datetime | None) -> dt.datetime | None:
        return _aware(v)


class ObligationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    action: str | None
    source: SourceType
    source_reference: str | None
    obligation_type: ObligationType
    status: ObligationStatus
    priority: Priority
    confidence: float
    due_at: dt.datetime | None
    due_precision: DuePrecision | None
    due_text: str | None
    due_timezone: str | None
    due_resolution: dict[str, Any] | None
    ambiguity: str | None
    owner: str
    counterparty_name: str | None
    counterparty_email: str | None
    requires_confirmation: bool
    recurrence: Recurrence | None
    entities: list[Any]
    snoozed_until: dt.datetime | None
    last_notified_at: dt.datetime | None
    next_action_at: dt.datetime | None
    completed_at: dt.datetime | None
    dismissed_at: dt.datetime | None
    completed_via: str | None
    acknowledged_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ObligationList(BaseModel):
    items: list[ObligationOut]
    total: int
    limit: int
    offset: int


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: SourceType
    external_id: str
    thread_id: str | None
    sender_email: str | None
    sender_name: str | None
    subject: str | None
    excerpt: str | None
    received_at: dt.datetime | None
    disposition: SourceDisposition
    role: str
    created_at: dt.datetime


class SuggestedAction(BaseModel):
    code: str
    label: str
    reason: str
    endpoint: str | None = None


class ActionResult(BaseModel):
    """Result of complete/dismiss/etc.: the updated obligation plus what changed."""

    obligation: ObligationOut
    changed: bool
    spawned_next: ObligationOut | None = None
