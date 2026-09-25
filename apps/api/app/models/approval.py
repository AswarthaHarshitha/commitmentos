from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import ApprovalAction, ApprovalStatus
from app.models.base import Base, TimestampMixin, enum_col, new_uuid


class ApprovalRequest(TimestampMixin, Base):
    """A proposed external action waiting for an explicit human decision.

    Lifecycle: PENDING -> APPROVED -> EXECUTING -> EXECUTED | FAILED  (or REJECTED / EXPIRED /
    CANCELLED). n8n only ever executes rows the *user* moved to APPROVED, and must "claim" them
    (APPROVED -> EXECUTING) atomically first, so a duplicate webhook cannot run an action twice.
    """

    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"))
    # the workflow run that proposed this (so the run can be resolved once the human has decided)
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("automation_runs.id", ondelete="SET NULL"), default=None)
    action_type: Mapped[ApprovalAction] = mapped_column(enum_col(ApprovalAction, "approval_action"))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_col(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING
    )
    proposed_by: Mapped[str] = mapped_column(String(16), default="SYSTEM")  # AI | SYSTEM | USER
    title: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str | None] = mapped_column(Text, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    executed_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        Index("ix_approval_requests_user_id_status", "user_id", "status"),
        Index("ix_approval_requests_obligation_id", "obligation_id"),
        Index("ix_approval_requests_automation_run_id", "automation_run_id"),
        Index("ix_approval_requests_status_expires_at", "status", "expires_at"),
        Index("ix_approval_requests_created_at", "created_at"),
    )
