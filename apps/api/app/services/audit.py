"""Audit trail: one call per important action. Rows are immutable (DB trigger)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.clock import utcnow
from app.enums import ActorType, AuditEventType
from app.models import AuditEvent, User


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None

    @classmethod
    def user(cls, user: User) -> Actor:
        return cls(ActorType.USER, str(user.id))

    @classmethod
    def system(cls, name: str = "system") -> Actor:
        return cls(ActorType.SYSTEM, name)

    @classmethod
    def n8n(cls, execution_id: str | None = None) -> Actor:
        return cls(ActorType.N8N, execution_id)

    @classmethod
    def ai(cls, model: str | None = None) -> Actor:
        return cls(ActorType.AI, model)


def _default(value: Any) -> Any:
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def jsonable(data: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(data or {}, default=_default))


def record(
    db: Session,
    event_type: AuditEventType,
    message: str,
    *,
    user_id: uuid.UUID | None,
    actor: Actor,
    obligation_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
    source_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        obligation_id=obligation_id,
        source_id=source_id,
        automation_run_id=run_id,
        event_type=event_type,
        actor_type=actor.type,
        actor_id=actor.id,
        message=message,
        data=jsonable(data),
        created_at=now or utcnow(),
    )
    db.add(event)
    db.flush()
    return event
