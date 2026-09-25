from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.enums import ActorType, AuditEventType
from app.models.base import Base, enum_col


class AuditEvent(Base):
    """Append-only record of every important system action.

    Immutability is enforced in the database (see the migration): a trigger rejects UPDATE
    and DELETE, so even a bug in application code cannot rewrite history.
    ``created_at`` comes from the app clock so demo time-travel produces a coherent story.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), default=None)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="RESTRICT"), default=None
    )
    # RESTRICT (not SET NULL): a cascaded SET NULL would be an UPDATE on an immutable row.
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), default=None)
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("automation_runs.id", ondelete="RESTRICT"), default=None
    )
    event_type: Mapped[AuditEventType] = mapped_column(enum_col(AuditEventType, "audit_event_type", 48))
    actor_type: Mapped[ActorType] = mapped_column(enum_col(ActorType, "actor_type"))
    actor_id: Mapped[str | None] = mapped_column(String(64), default=None)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (
        Index("ix_audit_events_user_id_created_at", "user_id", "created_at"),
        # keyset pagination of the audit feed (ORDER BY id DESC with an id cursor) walks this index backwards
        Index("ix_audit_events_user_id_id", "user_id", "id"),
        Index("ix_audit_events_obligation_id_created_at", "obligation_id", "created_at"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_created_at", "created_at"),
    )
