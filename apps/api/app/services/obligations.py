"""Obligation use-cases. Route handlers and n8n webhooks call these; nothing else mutates obligations.

Conventions
* Every lookup is scoped to the owning user (``get_owned``) - a foreign id is a 404, not a 403,
  so ids cannot be probed.
* Mutations lock the row (``FOR UPDATE``): a double-click or a duplicate webhook serialises and
  the second call becomes an idempotent no-op instead of a second state change.
* Services ``flush()``; the caller ``commit()``s.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AuditEventType,
    DuePrecision,
    NotificationKind,
    NotificationStatus,
    ObligationType,
    Priority,
    SourceType,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import ConflictError, InvalidTransition, NotFoundError, ValidationFailed
from app.models import Notification, Obligation, User
from app.schemas.obligation import DueInput, ObligationCreate, ObligationPatch
from app.services import audit, dedup, lifecycle, monitor
from app.services.audit import Actor
from app.services.timeutil import (
    end_of_local_day,
    get_zone,
    local_date,
    local_to_utc,
    next_occurrence,
    wall_time_status,
)

_REMINDER_KINDS = [
    NotificationKind.REMINDER,
    NotificationKind.HIGH_PRIORITY_REMINDER,
    NotificationKind.OVERDUE,
    NotificationKind.ESCALATION,
]
MAX_SNOOZE = timedelta(days=30)


@dataclass
class DueValue:
    at: datetime
    precision: DuePrecision
    timezone: str
    warnings: list[str] = field(default_factory=list)


def user_zone(user: User, settings: Settings) -> ZoneInfo:
    return get_zone(user.timezone, settings.default_timezone)


def get_owned(db: Session, user: User, obligation_id: uuid.UUID, *, lock: bool = False) -> Obligation:
    stmt = select(Obligation).where(Obligation.id == obligation_id, Obligation.user_id == user.id)
    if lock:
        stmt = stmt.with_for_update()
    ob = db.scalar(stmt)
    if ob is None:
        raise NotFoundError("Obligation not found")
    return ob


def resolve_due(tz: ZoneInfo, due_at: datetime | None, due: DueInput | None) -> DueValue | None:
    if due_at is not None:
        return DueValue(due_at.astimezone(UTC), DuePrecision.DATETIME, tz.key)
    if due is None:
        return None
    if due.time is None:
        return DueValue(end_of_local_day(due.date, tz), DuePrecision.DATE, tz.key)
    naive = datetime.combine(due.date, due.time.replace(tzinfo=None))
    warnings = []
    status = wall_time_status(naive, tz)
    if status == "gap":
        warnings.append(f"{naive:%H:%M} does not exist on {due.date} in {tz.key} (clocks spring forward); shifted forward")
    elif status == "ambiguous":
        warnings.append(f"{naive:%H:%M} happens twice on {due.date} in {tz.key} (clocks fall back); used the first occurrence")
    return DueValue(local_to_utc(naive, tz), DuePrecision.DATETIME, tz.key, warnings)


def _fingerprint(title: str, due_at: datetime | None, tz: ZoneInfo) -> str:
    return dedup.fingerprint(title, local_date(due_at, tz) if due_at else None)


def create_manual(db: Session, user: User, data: ObligationCreate, now: datetime, settings: Settings) -> Obligation:
    tz = user_zone(user, settings)
    due = resolve_due(tz, data.due_at, data.due)
    ob = Obligation(
        user_id=user.id,
        title=data.title,
        description=data.description,
        action=data.action,
        source=SourceType.MANUAL,
        source_reference=None,
        obligation_type=data.obligation_type,
        priority=data.priority,
        status=S.OPEN,
        confidence=1.0,
        due_at=due.at if due else None,
        due_precision=due.precision if due else None,
        due_timezone=due.timezone if due else None,
        due_resolution={"method": "MANUAL", "timezone": tz.key, "warnings": due.warnings} if due else None,
        owner="me",
        counterparty_name=data.counterparty_name,
        counterparty_email=str(data.counterparty_email).lower() if data.counterparty_email else None,
        requires_confirmation=data.requires_confirmation,
        recurrence=data.recurrence,
        entities=[],
        acknowledged_at=now,
        fingerprint=_fingerprint(data.title, due.at if due else None, tz),
        created_at=now,
        updated_at=now,
    )
    db.add(ob)
    db.flush()
    audit.record(
        db,
        AuditEventType.OBLIGATION_CREATED,
        "Obligation created manually",
        user_id=user.id,
        obligation_id=ob.id,
        actor=Actor.user(user),
        data={"title": ob.title, "due_at": ob.due_at, "type": ob.obligation_type},
        now=now,
    )
    monitor.refresh_schedule(ob, user, now, settings)
    return ob


def _cancel_reminders(db: Session, ob: Obligation, now: datetime, reason: str) -> int:
    return (
        db.execute(
            update(Notification)
            .where(
                Notification.obligation_id == ob.id,
                Notification.kind.in_(_REMINDER_KINDS),
                Notification.status.in_([NotificationStatus.PENDING, NotificationStatus.SENDING]),
            )
            .values(status=NotificationStatus.CANCELLED, last_error=reason, updated_at=now)
        ).rowcount
        or 0
    )


def acknowledge(ob: Obligation, now: datetime) -> None:
    if ob.acknowledged_at is None:
        ob.acknowledged_at = now


def apply_patch(db: Session, user: User, ob: Obligation, patch: ObligationPatch, now: datetime, settings: Settings) -> list[str]:
    """Apply an edit. Returns the list of changed field names (empty = no-op)."""
    if ob.status in TERMINAL_STATUSES:
        raise ConflictError("Reopen this obligation before editing it", code="OBLIGATION_CLOSED")
    tz = user_zone(user, settings)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    def set_field(name: str, value: Any) -> None:
        if getattr(ob, name) != value:
            before[name], after[name] = getattr(ob, name), value
            setattr(ob, name, value)

    provided = patch.model_fields_set
    for name in ("title", "description", "action", "obligation_type", "priority", "requires_confirmation", "counterparty_name"):
        if name in provided and getattr(patch, name) is not None:
            set_field(name, getattr(patch, name))
    if "counterparty_email" in provided and patch.counterparty_email is not None:
        set_field("counterparty_email", str(patch.counterparty_email).lower())
    if patch.clear_recurrence:
        set_field("recurrence", None)
    elif patch.recurrence is not None:
        set_field("recurrence", patch.recurrence)

    due_changed = False
    if patch.clear_due:
        if ob.due_at is not None:
            before["due_at"], after["due_at"] = ob.due_at, None
            ob.due_at = ob.due_precision = ob.due_timezone = None
            ob.due_resolution = {"method": "CLEARED_BY_USER"}
            due_changed = True
    elif patch.due_at is not None or patch.due is not None:
        due = resolve_due(tz, patch.due_at, patch.due)
        assert due is not None
        if ob.due_at != due.at or ob.due_precision != due.precision:
            before["due_at"], after["due_at"] = ob.due_at, due.at
            ob.due_at, ob.due_precision, ob.due_timezone = due.at, due.precision, due.timezone
            ob.due_resolution = {
                "method": "USER_EDIT",
                "timezone": due.timezone,
                "previous_due_at": before["due_at"].isoformat() if before["due_at"] else None,
                "warnings": due.warnings,
            }
            due_changed = True
    if due_changed:
        ob.ambiguity = None  # the user has just resolved whatever was unclear
        set_field("snoozed_until", None)

    if not after and not due_changed:
        acknowledge(ob, now)
        return []

    if "title" in after or due_changed:
        ob.fingerprint = _fingerprint(ob.title, ob.due_at, tz)
    acknowledge(ob, now)

    if due_changed:
        cancelled = _cancel_reminders(db, ob, now, "deadline changed")
        if ob.status in (S.ACTION_REQUIRED, S.OVERDUE, S.ESCALATED) and (ob.due_at is None or ob.due_at > now):
            lifecycle.transition(db, ob, S.OPEN, actor=Actor.user(user), now=now, reason="deadline changed - re-evaluating reminders")
        after["reminders_cancelled"] = cancelled

    audit.record(
        db,
        AuditEventType.OBLIGATION_UPDATED,
        "Edited: " + ", ".join(k for k in after if k != "reminders_cancelled"),
        user_id=user.id,
        obligation_id=ob.id,
        actor=Actor.user(user),
        data={"before": before, "after": after},
        now=now,
    )
    monitor.refresh_schedule(ob, user, now, settings)
    db.flush()
    return sorted(k for k in after if k != "reminders_cancelled")


def accept(db: Session, user: User, ob: Obligation, now: datetime, settings: Settings, actor: Actor | None = None) -> bool:
    """User confirms a detection is real (NEEDS_REVIEW/DETECTED -> OPEN) or acknowledges an auto-created one."""
    actor = actor or Actor.user(user)
    if ob.status in TERMINAL_STATUSES:
        raise InvalidTransition(f"A {ob.status.value.lower()} obligation cannot be accepted; reopen it instead")
    changed = False
    if ob.status in (S.DETECTED, S.NEEDS_REVIEW):
        changed = lifecycle.transition(db, ob, S.OPEN, actor=actor, now=now)
        if ob.requires_confirmation:
            lifecycle.transition(db, ob, S.ACTION_REQUIRED, actor=actor, now=now, reason="a reply/confirmation is expected")
    elif ob.acknowledged_at is None:
        audit.record(
            db, AuditEventType.ACCEPTED, "Acknowledged by user", user_id=ob.user_id, obligation_id=ob.id, actor=actor, now=now
        )
        changed = True
    acknowledge(ob, now)
    monitor.refresh_schedule(ob, user, now, settings)
    return changed


def complete(
    db: Session,
    user: User,
    ob: Obligation,
    now: datetime,
    settings: Settings,
    *,
    via: str,
    actor: Actor | None = None,
    note: str | None = None,
) -> tuple[bool, Obligation | None]:
    """Complete an obligation. Idempotent. Returns (changed, spawned_next_occurrence)."""
    actor = actor or Actor.user(user)
    changed = lifecycle.transition(db, ob, S.COMPLETED, actor=actor, now=now, via=via, data={"note": note} if note else None)
    acknowledge(ob, now)
    spawned = None
    if changed and ob.recurrence and ob.due_at:
        spawned = _spawn_next_occurrence(db, user, ob, now, settings)
    return changed, spawned


def _spawn_next_occurrence(db: Session, user: User, ob: Obligation, now: datetime, settings: Settings) -> Obligation:
    tz = user_zone(user, settings)
    next_due = ob.due_at
    assert next_due is not None and ob.recurrence is not None
    for _ in range(120):  # a long-overdue recurring item catches up to the future instead of spawning in the past
        next_due = next_occurrence(next_due, ob.recurrence, tz)
        if next_due > now:
            break
    nxt = Obligation(
        user_id=ob.user_id,
        title=ob.title,
        description=ob.description,
        action=ob.action,
        source=ob.source,
        source_reference=f"recurrence:{ob.id}",
        obligation_type=ob.obligation_type,
        priority=ob.priority,
        status=S.OPEN,
        confidence=1.0,
        due_at=next_due,
        due_precision=ob.due_precision,
        due_timezone=ob.due_timezone,
        due_resolution={"method": "RECURRENCE", "from": str(ob.id), "recurrence": ob.recurrence.value},
        owner=ob.owner,
        counterparty_name=ob.counterparty_name,
        counterparty_email=ob.counterparty_email,
        requires_confirmation=ob.requires_confirmation,
        recurrence=ob.recurrence,
        entities=ob.entities,
        acknowledged_at=now,
        fingerprint=_fingerprint(ob.title, next_due, tz),
        created_at=now,
        updated_at=now,
    )
    db.add(nxt)
    db.flush()
    for target, other, text in ((ob, nxt, "Next occurrence created"), (nxt, ob, "Created from recurring obligation")):
        audit.record(
            db,
            AuditEventType.RECURRENCE_SPAWNED,
            f"{text} ({ob.recurrence.value.lower()})",
            user_id=ob.user_id,
            obligation_id=target.id,
            actor=Actor.system("recurrence"),
            data={"other_obligation": other.id, "next_due_at": next_due},
            now=now,
        )
    monitor.refresh_schedule(nxt, user, now, settings)
    return nxt


def dismiss(db: Session, user: User, ob: Obligation, now: datetime, *, reason: str | None = None, actor: Actor | None = None) -> bool:
    actor = actor or Actor.user(user)
    changed = lifecycle.transition(db, ob, S.DISMISSED, actor=actor, now=now, reason=reason)
    acknowledge(ob, now)
    return changed


def reopen(db: Session, user: User, ob: Obligation, now: datetime, settings: Settings, actor: Actor | None = None) -> bool:
    actor = actor or Actor.user(user)
    if ob.status not in TERMINAL_STATUSES:
        raise InvalidTransition("Only completed or dismissed obligations can be reopened")
    changed = lifecycle.transition(db, ob, S.OPEN, actor=actor, now=now)
    acknowledge(ob, now)
    monitor.refresh_schedule(ob, user, now, settings)
    return changed


def snooze(
    db: Session,
    user: User,
    ob: Obligation,
    now: datetime,
    settings: Settings,
    *,
    hours: int | None,
    until: datetime | None,
) -> datetime:
    if ob.status not in ACTIVE_STATUSES:
        raise InvalidTransition(f"Only tracked obligations can be snoozed (this one is {ob.status.value})")
    target = until.astimezone(UTC) if until is not None else now + timedelta(hours=hours or settings.default_snooze_hours)
    if target <= now:
        raise ValidationFailed("Snooze time must be in the future")
    if target - now > MAX_SNOOZE:
        raise ValidationFailed("Snooze cannot be longer than 30 days")
    ob.snoozed_until = target
    acknowledge(ob, now)
    audit.record(
        db,
        AuditEventType.SNOOZED,
        "Reminders snoozed",
        user_id=ob.user_id,
        obligation_id=ob.id,
        actor=Actor.user(user),
        data={"until": target},
        now=now,
    )
    monitor.refresh_schedule(ob, user, now, settings)
    return target


# ------------------------------------------------------------------ queries

VIEWS = {"inbox", "active", "review", "overdue", "closed", "upcoming", "all"}


def _escape_like(text: str) -> str:
    return re.sub(r"([\\%_])", r"\\\1", text)


def list_query(
    user: User,
    *,
    view: str = "all",
    statuses: list[S] | None = None,
    types: list[ObligationType] | None = None,
    priorities: list[Priority] | None = None,
    sources: list[SourceType] | None = None,
    q: str | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    has_due: bool | None = None,
    now: datetime,
) -> Select[tuple[Obligation]]:
    stmt = select(Obligation).where(Obligation.user_id == user.id)
    if view == "inbox":
        stmt = stmt.where(Obligation.acknowledged_at.is_(None), Obligation.status.not_in(list(TERMINAL_STATUSES)))
    elif view == "active":
        stmt = stmt.where(Obligation.status.in_(list(ACTIVE_STATUSES)))
    elif view == "review":
        stmt = stmt.where(Obligation.status.in_([S.DETECTED, S.NEEDS_REVIEW]))
    elif view == "overdue":
        stmt = stmt.where(Obligation.status.in_([S.OVERDUE, S.ESCALATED]))
    elif view == "closed":
        stmt = stmt.where(Obligation.status.in_(list(TERMINAL_STATUSES)))
    elif view == "upcoming":
        stmt = stmt.where(
            Obligation.status.in_([S.OPEN, S.ACTION_REQUIRED, S.SCHEDULED]),
            Obligation.due_at.is_not(None),
            Obligation.due_at > now,
        )
    if statuses:
        stmt = stmt.where(Obligation.status.in_(statuses))
    if types:
        stmt = stmt.where(Obligation.obligation_type.in_(types))
    if priorities:
        stmt = stmt.where(Obligation.priority.in_(priorities))
    if sources:
        stmt = stmt.where(Obligation.source.in_(sources))
    if q:
        pattern = f"%{_escape_like(q.strip())}%"
        stmt = stmt.where(or_(Obligation.title.ilike(pattern, escape="\\"), Obligation.description.ilike(pattern, escape="\\")))
    if due_before is not None:
        stmt = stmt.where(Obligation.due_at <= due_before)
    if due_after is not None:
        stmt = stmt.where(Obligation.due_at >= due_after)
    if has_due is True:
        stmt = stmt.where(Obligation.due_at.is_not(None))
    elif has_due is False:
        stmt = stmt.where(Obligation.due_at.is_(None))
    return stmt


def paginate(db: Session, stmt: Select[tuple[Obligation]], *, sort: str, limit: int, offset: int) -> tuple[list[Obligation], int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    if sort == "created":
        stmt = stmt.order_by(Obligation.created_at.desc())
    elif sort == "priority":
        stmt = stmt.order_by(Obligation.priority.desc(), Obligation.due_at.asc().nulls_last())
    else:  # due (default): soonest first, undated last
        stmt = stmt.order_by(Obligation.due_at.asc().nulls_last(), Obligation.created_at.desc())
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return list(rows), total


def counts_by_status(db: Session, user: User) -> dict[str, int]:
    rows = db.execute(
        select(Obligation.status, func.count()).where(Obligation.user_id == user.id).group_by(Obligation.status)
    ).all()
    return {status.value: n for status, n in rows}

