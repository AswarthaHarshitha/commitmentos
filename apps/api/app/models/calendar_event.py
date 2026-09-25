from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import CalendarEventStatus, CalendarProvider
from app.models.base import Base, TimestampMixin, enum_col, new_uuid


class CalendarEvent(TimestampMixin, Base):
    __tablename__ = "calendar_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="SET NULL"), default=None
    )
    provider: Mapped[CalendarProvider] = mapped_column(enum_col(CalendarProvider, "calendar_provider"))
    external_id: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(300))
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    location: Mapped[str | None] = mapped_column(String(300), default=None)
    url: Mapped[str | None] = mapped_column(String(1024), default=None)
    status: Mapped[CalendarEventStatus] = mapped_column(
        enum_col(CalendarEventStatus, "calendar_event_status"), default=CalendarEventStatus.CONFIRMED
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(default=None)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_id", name="uq_calendar_events_user_provider_external"),
        Index("ix_calendar_events_user_id_start_at", "user_id", "start_at"),
        Index("ix_calendar_events_obligation_id", "obligation_id"),
    )
