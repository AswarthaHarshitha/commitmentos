"""Events n8n sends to POST /api/webhooks/n8n. One strictly-typed model per event, discriminated
by ``event``; unknown fields are rejected so a workflow bug shows up as a 422, not silent drift."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.enums import ApprovalAction, RunStatus


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStarted(_Event):
    event: Literal["run.started"]
    workflow_key: str = Field(max_length=64)
    n8n_execution_id: str = Field(max_length=64)
    n8n_workflow_id: str | None = Field(default=None, max_length=64)
    workflow_name: str | None = Field(default=None, max_length=200)
    trigger: str = Field(default="WEBHOOK", max_length=32)
    user_email: EmailStr | None = None
    correlation_id: str | None = Field(default=None, max_length=128)
    obligation_id: uuid.UUID | None = None
    status: Literal["RUNNING", "WAITING"] = "RUNNING"


class RunFinished(_Event):
    event: Literal["run.finished", "run.failed"]
    n8n_execution_id: str = Field(max_length=64)
    workflow_key: str | None = Field(default=None, max_length=64)
    n8n_workflow_id: str | None = Field(default=None, max_length=64)
    workflow_name: str | None = Field(default=None, max_length=200)
    trigger: str | None = Field(default=None, max_length=32)
    status: RunStatus | None = None
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4000)
    error_node: str | None = Field(default=None, max_length=200)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    user_email: EmailStr | None = None
    obligation_id: uuid.UUID | None = None


class NotificationReport(_Event):
    event: Literal["notification.sent", "notification.failed"]
    notification_id: uuid.UUID
    error: str | None = Field(default=None, max_length=1000)
    n8n_execution_id: str | None = Field(default=None, max_length=64)


class ApprovalReport(_Event):
    event: Literal["approval.executed", "approval.failed"]
    approval_id: uuid.UUID
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=1000)
    n8n_execution_id: str | None = Field(default=None, max_length=64)


class ProposalCreated(_Event):
    """A workflow (calendar sync, completion detection, follow-up assistant) proposes an action.
    It only ever creates a PENDING approval request; nothing external happens until the user approves."""

    event: Literal["proposal.created"]
    obligation_id: uuid.UUID
    action_type: ApprovalAction
    title: str = Field(min_length=1, max_length=300)
    rationale: str | None = Field(default=None, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    proposed_by: Literal["AI", "SYSTEM"] = "SYSTEM"
    n8n_execution_id: str | None = Field(default=None, max_length=64)


class MessageExtracted(_Event):
    """n8n reports the outcome of analysing one message. The API re-validates and re-decides everything."""

    event: Literal["message.extracted"]
    user_email: EmailStr
    message: dict[str, Any]
    extraction_status: str = Field(default="OK", max_length=32)
    extraction_error: str | None = Field(default=None, max_length=1000)
    extraction: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    n8n_execution_id: str | None = Field(default=None, max_length=64)


WebhookEvent = Annotated[
    RunStarted | RunFinished | NotificationReport | ApprovalReport | ProposalCreated | MessageExtracted,
    Field(discriminator="event"),
]
