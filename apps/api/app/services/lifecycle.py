"""Obligation state machine. The ONLY place that changes ``Obligation.status``.

Every transition is validated against ``ALLOWED``, applies its side-effects (timestamps,
stopping reminders, recomputing the next monitor wake-up) and writes an audit event - so a
status can never change silently and reminders can never outlive a completed obligation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.enums import (
    ApprovalStatus,
    AuditEventType,
    NotificationKind,
    NotificationStatus,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import InvalidTransition
from app.models import ApprovalRequest, Notification, Obligation
from app.services import audit, automation
from app.services.audit import Actor

ALLOWED: dict[S, frozenset[S]] = {
    S.DETECTED: frozenset({S.OPEN, S.NEEDS_REVIEW, S.DISMISSED}),
    S.NEEDS_REVIEW: frozenset({S.OPEN, S.DISMISSED}),
    S.OPEN: frozenset({S.ACTION_REQUIRED, S.SCHEDULED, S.OVERDUE, S.COMPLETED, S.DISMISSED}),
    S.ACTION_REQUIRED: frozenset({S.OPEN, S.SCHEDULED, S.OVERDUE, S.COMPLETED, S.DISMISSED}),
    S.SCHEDULED: frozenset({S.OPEN, S.ACTION_REQUIRED, S.OVERDUE, S.COMPLETED, S.DISMISSED}),
    S.OVERDUE: frozenset({S.OPEN, S.ESCALATED, S.COMPLETED, S.DISMISSED}),
    S.ESCALATED: frozenset({S.OPEN, S.COMPLETED, S.DISMISSED}),
    S.COMPLETED: frozenset({S.OPEN}),  # reopen
    S.DISMISSED: frozenset({S.OPEN}),  # restore
}

# Notifications that stop mattering once an obligation is closed. ACTION_RESULT ("your email was
# sent") is informational and is kept.
_CANCELLABLE_KINDS = [k for k in NotificationKind if k != NotificationKind.ACTION_RESULT]

_AUDIT_EVENT: dict[S, AuditEventType] = {
    S.COMPLETED: AuditEventType.COMPLETED,
    S.DISMISSED: AuditEventType.DISMISSED,
    S.OVERDUE: AuditEventType.OVERDUE_MARKED,
    S.ESCALATED: AuditEventType.ESCALATED,
}


def can_transition(src: S, dst: S) -> bool:
    return dst in ALLOWED.get(src, frozenset())


def cancel_pending_children(db: Session, ob: Obligation, now: datetime, reason: str) -> dict[str, int]:
    """Stop everything still queued for a closed obligation."""
    notif = db.execute(
        update(Notification)
        .where(
            Notification.obligation_id == ob.id,
            Notification.status.in_([NotificationStatus.PENDING, NotificationStatus.SENDING]),
            Notification.kind.in_(_CANCELLABLE_KINDS),
        )
        .values(status=NotificationStatus.CANCELLED, last_error=reason, updated_at=now)
    ).rowcount
    # Only approvals still awaiting a human decision are withdrawn. One the user already approved
    # is a deliberate instruction and is left to complete.
    appr = db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.obligation_id == ob.id, ApprovalRequest.status == ApprovalStatus.PENDING)
        .values(status=ApprovalStatus.CANCELLED, error=reason, updated_at=now)
    ).rowcount
    if appr:
        automation.resolve_waiting(db, now)
    return {"notifications_cancelled": notif or 0, "approvals_cancelled": appr or 0}


def transition(
    db: Session,
    ob: Obligation,
    to: S,
    *,
    actor: Actor,
    now: datetime,
    via: str | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    """Move ``ob`` to ``to``. Returns False (and does nothing) if it is already there."""
    src = ob.status
    if src == to:
        return False
    if not can_transition(src, to):
        raise InvalidTransition(
            f"Cannot move an obligation from {src.value} to {to.value}",
            extra={"from": src.value, "to": to.value},
        )

    ob.status = to
    payload: dict[str, Any] = {"from": src.value, "to": to.value, **(data or {})}
    if reason:
        payload["reason"] = reason

    if to == S.COMPLETED:
        ob.completed_at = now
        ob.completed_via = via
        ob.snoozed_until = None
        ob.next_action_at = None
        payload["via"] = via
    elif to == S.DISMISSED:
        ob.dismissed_at = now
        ob.snoozed_until = None
        ob.next_action_at = None
    elif src in (S.COMPLETED, S.DISMISSED) and to == S.OPEN:
        ob.completed_at = None
        ob.completed_via = None
        ob.dismissed_at = None

    if to in (S.COMPLETED, S.DISMISSED):
        payload.update(cancel_pending_children(db, ob, now, f"obligation {to.value.lower()}"))

    if src in (S.DETECTED, S.NEEDS_REVIEW) and to == S.OPEN:
        event = AuditEventType.ACCEPTED
    elif src in (S.COMPLETED, S.DISMISSED) and to == S.OPEN:
        event = AuditEventType.REOPENED
    else:
        event = _AUDIT_EVENT.get(to, AuditEventType.STATUS_CHANGED)

    audit.record(
        db,
        event,
        _describe(src, to, via, reason),
        user_id=ob.user_id,
        obligation_id=ob.id,
        actor=actor,
        data=payload,
        now=now,
    )
    if to in (S.COMPLETED, S.DISMISSED) and payload.get("notifications_cancelled"):
        audit.record(
            db,
            AuditEventType.REMINDERS_STOPPED,
            f"Stopped {payload['notifications_cancelled']} queued notification(s)",
            user_id=ob.user_id,
            obligation_id=ob.id,
            actor=Actor.system("lifecycle"),
            data={"count": payload["notifications_cancelled"]},
            now=now,
        )
    db.flush()
    return True


def _describe(src: S, to: S, via: str | None, reason: str | None) -> str:
    suffix = f" via {via.lower().replace('_', ' ')}" if via else ""
    if to == S.COMPLETED:
        return f"Marked complete{suffix}"
    if to == S.DISMISSED:
        return "Dismissed" + (f": {reason}" if reason else "")
    if to == S.OVERDUE:
        return "Deadline passed - marked overdue"
    if to == S.ESCALATED:
        return "Still unresolved after the escalation period - escalated"
    if src in (S.DETECTED, S.NEEDS_REVIEW) and to == S.OPEN:
        return "Accepted as a tracked obligation"
    if to == S.OPEN and src in (S.COMPLETED, S.DISMISSED):
        return f"Reopened (was {src.value.lower()})"
    return f"Status changed from {src.value} to {to.value}"
