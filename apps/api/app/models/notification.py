from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.enums import NotificationChannel, NotificationKind, NotificationStatus
from app.models.base import Base, TimestampMixin, enum_col, new_uuid


class Notification(TimestampMixin, Base):
    """One outbound message on one channel.

    ``dedupe_key`` is what stops reminder spam: the policy layer derives it from
    (obligation, kind, deadline, channel), and the unique constraint makes double-creation
    impossible even under concurrent monitor ticks.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="CASCADE"), default=None
    )
    kind: Mapped[NotificationKind] = mapped_column(enum_col(NotificationKind, "notification_kind"))
    channel: Mapped[NotificationChannel] = mapped_column(enum_col(NotificationChannel, "notification_channel"))
    status: Mapped[NotificationStatus] = mapped_column(
        enum_col(NotificationStatus, "notification_status"), default=NotificationStatus.PENDING
    )
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    dedupe_key: Mapped[str] = mapped_column(String(255))

    scheduled_for: Mapped[datetime] = mapped_column(default=utcnow)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    next_attempt_at: Mapped[datetime | None] = mapped_column(default=None)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    read_at: Mapped[datetime | None] = mapped_column(default=None)
    automation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("automation_runs.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        Index("ix_notifications_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_notifications_obligation_id", "obligation_id"),
        Index("ix_notifications_user_id_status", "user_id", "status"),
    )
