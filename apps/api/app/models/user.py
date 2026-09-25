from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    # Validated by schemas.UserPreferences: business_day_end, date_order, reminder offsets, channels...
    preferences: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # Bumped on logout / password change; JWTs carrying an older value are rejected.
    token_version: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
