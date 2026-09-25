from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import (
    DuePrecision,
    ObligationStatus,
    ObligationType,
    Priority,
    Recurrence,
    SourceType,
)
from app.models.base import Base, TimestampMixin, enum_col, new_uuid

if TYPE_CHECKING:
    from app.models.source import Source

_ACTIVE_SQL = "status IN ('OPEN','ACTION_REQUIRED','SCHEDULED','OVERDUE','ESCALATED')"


class Obligation(TimestampMixin, Base):
    """A tracked commitment. Everything time-related on this row is UTC (timestamptz)."""

    __tablename__ = "obligations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    action: Mapped[str | None] = mapped_column(String(500), default=None)  # extracted action

    source: Mapped[SourceType] = mapped_column(enum_col(SourceType, "source_type"))
    source_reference: Mapped[str | None] = mapped_column(String(512), default=None)
    obligation_type: Mapped[ObligationType] = mapped_column(
        enum_col(ObligationType, "obligation_type"), default=ObligationType.OTHER
    )
    status: Mapped[ObligationStatus] = mapped_column(
        enum_col(ObligationStatus, "obligation_status"), default=ObligationStatus.DETECTED
    )
    priority: Mapped[Priority] = mapped_column(enum_col(Priority, "priority"), default=Priority.MEDIUM)
    confidence: Mapped[float] = mapped_column(default=1.0)

    # --- deadline (final value computed by deterministic code, never by the LLM) ---
    due_at: Mapped[datetime | None] = mapped_column(default=None)
    due_precision: Mapped[DuePrecision | None] = mapped_column(
        enum_col(DuePrecision, "due_precision"), default=None
    )
    due_text: Mapped[str | None] = mapped_column(String(300), default=None)  # verbatim phrase
    due_timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    due_resolution: Mapped[dict[str, Any] | None] = mapped_column(default=None)  # how due_at was derived
    ambiguity: Mapped[str | None] = mapped_column(Text, default=None)

    owner: Mapped[str] = mapped_column(String(200), default="me", server_default="me")
    counterparty_name: Mapped[str | None] = mapped_column(String(200), default=None)
    counterparty_email: Mapped[str | None] = mapped_column(String(320), default=None)
    requires_confirmation: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    recurrence: Mapped[Recurrence | None] = mapped_column(enum_col(Recurrence, "recurrence"), default=None)
    entities: Mapped[list[Any]] = mapped_column(default=list, server_default=text("'[]'::jsonb"))

    fingerprint: Mapped[str] = mapped_column(String(64))  # dedup key (see services.dedup)

    snoozed_until: Mapped[datetime | None] = mapped_column(default=None)
    last_notified_at: Mapped[datetime | None] = mapped_column(default=None)
    next_action_at: Mapped[datetime | None] = mapped_column(default=None)  # when the monitor must look again
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    dismissed_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_via: Mapped[str | None] = mapped_column(String(32), default=None)
    # NULL = the user has not looked at this yet (drives the Commitment Inbox). Any deliberate
    # user action on the obligation (accept, edit, snooze, complete...) sets it.
    acknowledged_at: Mapped[datetime | None] = mapped_column(default=None)

    sources: Mapped[list[Source]] = relationship(
        "Source", back_populates="obligation", order_by="Source.created_at", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("status <> 'COMPLETED' OR completed_at IS NOT NULL", name="completed_has_timestamp"),
        CheckConstraint("(due_at IS NULL) = (due_precision IS NULL)", name="due_precision_consistent"),
        Index("ix_obligations_status", "status"),
        Index("ix_obligations_due_at", "due_at"),
        Index("ix_obligations_created_at", "created_at"),
        Index("ix_obligations_source", "source"),
        Index("ix_obligations_user_id", "user_id"),
        Index("ix_obligations_user_id_status", "user_id", "status"),
        Index("ix_obligations_user_id_due_at", "user_id", "due_at"),
        Index("ix_obligations_user_id_created_at", "user_id", "created_at"),
        Index("ix_obligations_user_id_source", "user_id", "source"),
        Index("ix_obligations_user_id_fingerprint", "user_id", "fingerprint"),
        Index(
            "ix_obligations_inbox",
            "user_id",
            "created_at",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
        # The deadline monitor's hot path: "active obligations whose next_action_at has arrived".
        Index(
            "ix_obligations_monitor",
            "next_action_at",
            postgresql_where=text(f"next_action_at IS NOT NULL AND {_ACTIVE_SQL}"),
        ),
    )
