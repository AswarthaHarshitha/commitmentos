from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.enums import RunStatus
from app.models.base import Base, enum_col, new_uuid


class AutomationRun(Base):
    """One execution of an n8n workflow (reported by the workflow itself) or of a backend job.

    ``n8n_execution_id`` is n8n's own execution id, so a row here can be cross-checked
    against the n8n editor's execution list.
    """

    __tablename__ = "automation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="SET NULL"), default=None
    )
    workflow_key: Mapped[str] = mapped_column(String(64))
    workflow_name: Mapped[str] = mapped_column(String(200))
    n8n_workflow_id: Mapped[str | None] = mapped_column(String(64), default=None)
    n8n_execution_id: Mapped[str | None] = mapped_column(String(64), default=None)
    trigger: Mapped[str] = mapped_column(String(32), default="WEBHOOK")
    status: Mapped[RunStatus] = mapped_column(enum_col(RunStatus, "run_status"), default=RunStatus.RUNNING)
    attempt: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_node: Mapped[str | None] = mapped_column(String(200), default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (
        Index("ix_automation_runs_workflow_key_started_at", "workflow_key", "started_at"),
        Index("ix_automation_runs_status", "status"),
        Index("ix_automation_runs_user_id_started_at", "user_id", "started_at"),
        Index("ix_automation_runs_correlation_id", "correlation_id"),
        Index("ix_automation_runs_obligation_id", "obligation_id"),
        Index(
            "uq_automation_runs_n8n_execution_id",
            "n8n_execution_id",
            unique=True,
            postgresql_where=text("n8n_execution_id IS NOT NULL"),
        ),
    )


class SystemSetting(Base):
    """Tiny key/value table (currently only the demo clock offset)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
