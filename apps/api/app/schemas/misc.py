"""Response/request models for notifications, approvals, audit, automation and calendar."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    ActorType,
    ApprovalAction,
    ApprovalStatus,
    AuditEventType,
    CalendarEventStatus,
    CalendarProvider,
    CandidateStatus,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    ObligationType,
    RunStatus,
)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    obligation_id: uuid.UUID | None
    kind: NotificationKind
    channel: NotificationChannel
    status: NotificationStatus
    title: str
    body: str
    attempts: int
    last_error: str | None
    scheduled_for: dt.datetime
    sent_at: dt.datetime | None
    read_at: dt.datetime | None
    created_at: dt.datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread: int


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    obligation_id: uuid.UUID
    automation_run_id: uuid.UUID | None = None  # the workflow run that proposed this
    action_type: ApprovalAction
    status: ApprovalStatus
    proposed_by: str
    title: str
    rationale: str | None
    payload: dict[str, Any]
    expires_at: dt.datetime | None
    decided_at: dt.datetime | None
    executed_at: dt.datetime | None
    attempts: int
    result: dict[str, Any] | None
    error: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ApprovalPatch(BaseModel):
    """Edit a proposed action (e.g. tweak the follow-up draft) before deciding on it."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=8000)
    start_at: dt.datetime | None = None
    end_at: dt.datetime | None = None


class CalendarEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    obligation_id: uuid.UUID | None
    provider: CalendarProvider
    external_id: str
    title: str
    start_at: dt.datetime
    end_at: dt.datetime
    timezone: str
    location: str | None
    url: str | None
    status: CalendarEventStatus
    last_verified_at: dt.datetime | None


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    obligation_id: uuid.UUID | None
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str | None
    message: str
    data: dict[str, Any]
    automation_run_id: uuid.UUID | None
    created_at: dt.datetime


class AuditList(BaseModel):
    items: list[AuditEventOut]
    next_before: int | None


class AutomationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    obligation_id: uuid.UUID | None
    workflow_key: str
    workflow_name: str
    n8n_workflow_id: str | None
    n8n_execution_id: str | None
    trigger: str
    status: RunStatus
    attempt: int
    started_at: dt.datetime
    finished_at: dt.datetime | None
    duration_ms: int | None
    result: dict[str, Any] | None
    error: str | None
    error_node: str | None
    correlation_id: str | None


    @classmethod
    def for_viewer(cls, run: Any) -> AutomationRunOut:
        """A run a user may see. A system-wide run (no owner: the scheduled monitor, the dispatcher, a workflow that
        failed before it knew whose message it was) is shared by everyone, so it shows status and counts only - never
        free text, which can quote another user's address or message (an SMTP error, a validation error's input)."""
        out = cls.model_validate(run)
        if run.user_id is None:
            out.error = "This workflow run failed; the operator log has the details." if run.error else None
            out.result = {k: v for k, v in (run.result or {}).items() if isinstance(v, bool | int | float)} or None
        return out


class AutomationRunList(BaseModel):
    items: list[AutomationRunOut]
    total: int


class TimelineEntry(BaseModel):
    at: dt.datetime
    kind: str  # "past" | "planned"
    event_type: str
    message: str
    actor: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CandidateOut(BaseModel):
    """A low-confidence detection: kept for review, not an active obligation."""

    id: uuid.UUID
    title: str
    action: str | None
    obligation_type: ObligationType
    confidence: float
    reason: str
    status: CandidateStatus
    explanation: str | None
    deadline_text: str | None
    due_at: dt.datetime | None
    sender_email: str | None
    subject: str | None
    excerpt: str | None
    received_at: dt.datetime | None
    created_at: dt.datetime
    promoted_obligation_id: uuid.UUID | None
