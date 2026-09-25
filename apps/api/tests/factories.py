"""Small helpers for building rows in tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.enums import (
    DuePrecision,
    ObligationStatus,
    ObligationType,
    Priority,
    SourceType,
)
from app.models import Obligation, User


def make_user(db: Session, email: str | None = None, timezone: str = "UTC", **kw: Any) -> User:
    user = User(
        email=(email or f"user-{uuid.uuid4().hex[:8]}@example.com").lower(),
        password_hash="x",
        timezone=timezone,
        **kw,
    )
    db.add(user)
    db.flush()
    return user


def make_obligation(
    db: Session,
    user: User,
    *,
    title: str = "Submit tax form",
    status: ObligationStatus = ObligationStatus.OPEN,
    due_at: datetime | None = None,
    **kw: Any,
) -> Obligation:
    if due_at is not None and "due_precision" not in kw:
        kw["due_precision"] = DuePrecision.DATETIME
    if status == ObligationStatus.COMPLETED and "completed_at" not in kw:
        kw["completed_at"] = datetime(2026, 1, 1, tzinfo=UTC)
    ob = Obligation(
        user_id=user.id,
        title=title,
        source=kw.pop("source", SourceType.MANUAL),
        obligation_type=kw.pop("obligation_type", ObligationType.DEADLINE),
        priority=kw.pop("priority", Priority.MEDIUM),
        status=status,
        due_at=due_at,
        fingerprint=kw.pop("fingerprint", uuid.uuid4().hex),
        **kw,
    )
    db.add(ob)
    db.flush()
    return ob
