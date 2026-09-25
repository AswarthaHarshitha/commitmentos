"""Deadline monitor: the deterministic heart of the lifecycle.

``run_tick`` is called on a schedule (by the n8n "Deadline Monitor" workflow). It is
idempotent - running it twice in a row does nothing the second time - and safe to run
concurrently (rows are locked with FOR UPDATE SKIP LOCKED, notifications are de-duplicated by
a unique key).

Per obligation it:
  1. moves the *state* forward by the clock (OPEN -> ACTION_REQUIRED -> OVERDUE -> ESCALATED),
  2. sends at most ONE notification: the most severe ladder rung that is due and unsent
     (a system that was offline for two days sends one "escalated" message, not four),
  3. records when it needs to look at the obligation again (``next_action_at``).
Nothing here calls an LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ACTIVE_STATUSES,
    ApprovalStatus,
    AuditEventType,
    NotificationKind,
)
from app.enums import (
    ObligationStatus as S,
)
from app.models import ApprovalRequest, Obligation, User
from app.services import audit, automation, lifecycle, messages, notifications
from app.services.audit import Actor
from app.services.reminders import (
    ReminderPolicy,
    build_ladder,
    due_rungs,
    next_rung_after,
    policy_for,
)

log = logging.getLogger("commitmentos.monitor")

STALE_DETECTED_AFTER = timedelta(minutes=10)
_MONITOR = Actor.system("deadline-monitor")


@dataclass
class TickSummary:
    evaluated: int = 0
    reminders_queued: int = 0
    marked_overdue: int = 0
    escalated: int = 0
    review_nudges: int = 0
    approvals_expired: int = 0
    stale_detected_swept: int = 0
    notifications_suppressed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "evaluated": self.evaluated,
            "reminders_queued": self.reminders_queued,
            "marked_overdue": self.marked_overdue,
            "escalated": self.escalated,
            "review_nudges": self.review_nudges,
            "approvals_expired": self.approvals_expired,
            "stale_detected_swept": self.stale_detected_swept,
            "notifications_suppressed": self.notifications_suppressed,
            "errors": self.errors,
        }


def compute_next_action_at(
    ob: Obligation, now: datetime, policy: ReminderPolicy, ladder: list | None = None
) -> datetime | None:
    """When must the monitor look at this obligation again? ``None`` = never (closed / no deadline)."""
    if ob.status not in ACTIVE_STATUSES or ob.due_at is None:
        return None
    ladder = ladder or build_ladder(ob.due_at, ob.due_precision, policy)
    escalate_at = ob.due_at + timedelta(hours=policy.escalate_after_hours)
    if now >= ob.due_at and ob.status in (S.OPEN, S.ACTION_REQUIRED, S.SCHEDULED):
        return now  # already past due but not yet marked
    if now >= escalate_at and ob.status == S.OVERDUE:
        return now
    candidates = []
    nxt = next_rung_after(ladder, now)
    if nxt is not None:
        candidates.append(nxt.at)
    if ob.snoozed_until is not None and ob.snoozed_until > now:
        candidates.append(ob.snoozed_until)
    return min(candidates) if candidates else None


def refresh_schedule(ob: Obligation, user: User, now: datetime, settings: Settings) -> None:
    ob.next_action_at = compute_next_action_at(ob, now, policy_for(user, settings))


def process_obligation(db: Session, ob: Obligation, now: datetime, settings: Settings, summary: TickSummary) -> None:
    user = db.get(User, ob.user_id)
    assert user is not None
    policy = policy_for(user, settings)
    if ob.due_at is None:
        ob.next_action_at = None
        return

    ladder = build_ladder(ob.due_at, ob.due_precision, policy)
    due = due_rungs(ladder, now)
    escalate_at = ob.due_at + timedelta(hours=policy.escalate_after_hours)

    # 1. time-driven state changes (facts, not notifications: they happen even while snoozed)
    if now >= ob.due_at and ob.status in (S.OPEN, S.ACTION_REQUIRED, S.SCHEDULED):
        lifecycle.transition(db, ob, S.OVERDUE, actor=_MONITOR, now=now, data={"due_at": ob.due_at})
        summary.marked_overdue += 1
    if now >= escalate_at and ob.status == S.OVERDUE:
        lifecycle.transition(db, ob, S.ESCALATED, actor=_MONITOR, now=now, data={"escalate_after_hours": policy.escalate_after_hours})
        summary.escalated += 1
    if ob.status == S.OPEN and any(r.kind in (NotificationKind.REMINDER, NotificationKind.HIGH_PRIORITY_REMINDER) for r in due):
        lifecycle.transition(db, ob, S.ACTION_REQUIRED, actor=_MONITOR, now=now, reason="entered reminder window")

    # 2. at most one notification: the most severe rung that is due
    snoozed = ob.snoozed_until is not None and ob.snoozed_until > now
    if due and not snoozed:
        rung = due[-1]
        content = messages.build(rung.kind, ob, policy.tz, now, settings)
        created = notifications.queue(
            db, user=user, ob=ob, kind=rung.kind, rung_key=rung.key, content=content, policy=policy, now=now
        )
        if created:
            summary.reminders_queued += 1
            audit.record(
                db,
                AuditEventType.NOTIFICATION_QUEUED,
                f"{rung.kind.value.replace('_', ' ').capitalize()} queued ({rung.key})",
                user_id=ob.user_id,
                obligation_id=ob.id,
                actor=_MONITOR,
                data={
                    "rung": rung.key,
                    "channels": [n.channel for n in created],
                    "superseded_rungs": [r.key for r in due[:-1]],
                },
                now=now,
            )
        else:
            summary.notifications_suppressed += 1

    # 3. schedule the next look
    ob.next_action_at = compute_next_action_at(ob, now, policy, ladder)


def _expire_approvals(db: Session, now: datetime, summary: TickSummary) -> None:
    stale = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for approval in stale:
        approval.status = ApprovalStatus.EXPIRED
        approval.error = "expired without a decision"
        audit.record(
            db,
            AuditEventType.APPROVAL_EXPIRED,
            f"Approval request expired without a decision: {approval.title}",
            user_id=approval.user_id,
            obligation_id=approval.obligation_id,
            actor=_MONITOR,
            data={"approval_id": approval.id, "action": approval.action_type},
            now=now,
        )
        summary.approvals_expired += 1
    if stale:
        automation.resolve_waiting(db, now)


def _sweep_stale_detected(db: Session, now: datetime, summary: TickSummary) -> None:
    """Safety net: an obligation must never sit in the transient DETECTED state."""
    stale = db.scalars(
        select(Obligation)
        .where(Obligation.status == S.DETECTED, Obligation.created_at <= now - STALE_DETECTED_AFTER)
        .with_for_update(skip_locked=True)
    ).all()
    for ob in stale:
        lifecycle.transition(db, ob, S.NEEDS_REVIEW, actor=_MONITOR, now=now, reason="stuck in DETECTED")
        summary.stale_detected_swept += 1


def _nudge_unreviewed(db: Session, now: datetime, settings: Settings, summary: TickSummary) -> None:
    """A NEEDS_REVIEW item with an approaching/passed deadline gets exactly one nudge."""
    candidates = db.scalars(
        select(Obligation)
        .where(
            Obligation.status == S.NEEDS_REVIEW,
            Obligation.due_at.is_not(None),
            Obligation.due_at <= now + timedelta(hours=max(settings.reminder_offsets_hours)),
            Obligation.due_at >= now - timedelta(days=7),
        )
        .with_for_update(skip_locked=True)
        .limit(200)
    ).all()
    for ob in candidates:
        user = db.get(User, ob.user_id)
        assert user is not None
        policy = policy_for(user, settings)
        content = messages.build(NotificationKind.NEEDS_REVIEW, ob, policy.tz, now, settings)
        created = notifications.queue(
            db, user=user, ob=ob, kind=NotificationKind.NEEDS_REVIEW, rung_key="REVIEW_NUDGE",
            content=content, policy=policy, now=now,
        )
        if created:
            summary.review_nudges += 1
            audit.record(
                db,
                AuditEventType.NOTIFICATION_QUEUED,
                "Unreviewed commitment has an approaching deadline - nudged once",
                user_id=ob.user_id,
                obligation_id=ob.id,
                actor=_MONITOR,
                data={"rung": "REVIEW_NUDGE"},
                now=now,
            )


def run_tick(db: Session, now: datetime, settings: Settings, batch_size: int = 200) -> TickSummary:
    summary = TickSummary()
    _expire_approvals(db, now, summary)
    _sweep_stale_detected(db, now, summary)

    due_obligations = db.scalars(
        select(Obligation)
        .where(
            Obligation.status.in_(ACTIVE_STATUSES),
            Obligation.next_action_at.is_not(None),
            Obligation.next_action_at <= now,
        )
        .order_by(Obligation.next_action_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()

    for ob in due_obligations:
        summary.evaluated += 1
        try:
            with db.begin_nested():  # one bad row must not abort the whole tick
                process_obligation(db, ob, now, settings, summary)
        except Exception as exc:  # noqa: BLE001 - isolate per-row failures, report them
            log.exception("monitor failed for obligation %s", ob.id)
            summary.errors.append(f"{ob.id}: {type(exc).__name__}")
            ob.next_action_at = now + timedelta(minutes=5)  # back off instead of hot-looping

    _nudge_unreviewed(db, now, settings, summary)
    db.flush()
    return summary
