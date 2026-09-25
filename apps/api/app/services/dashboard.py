"""Dashboard aggregation. Pure SQL + deterministic rules; the "focus" item and the headline are
computed here (not in the UI, not by an LLM) so they are testable and always agree with the clock."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ACTIVE_STATUSES,
    ApprovalStatus,
    ObligationType,
    Priority,
    RunStatus,
)
from app.enums import (
    ObligationStatus as S,
)
from app.models import ApprovalRequest, AutomationRun, Obligation, User
from app.schemas.misc import AutomationRunOut
from app.services import obligations as ob_service
from app.services.messages import fmt_when
from app.services.timeutil import day_bounds_utc, get_zone, local_date, to_local

_PRIORITY_WEIGHT = {Priority.URGENT: 3, Priority.HIGH: 2, Priority.MEDIUM: 1, Priority.LOW: 0}
_APPOINTMENT_TYPES = (ObligationType.APPOINTMENT, ObligationType.INTERVIEW)


def part_of_day(now: datetime, tz) -> str:
    hour = to_local(now, tz).hour
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def _urgency_group(ob: Obligation, now: datetime) -> int:
    if ob.status == S.ESCALATED:
        return 0
    if ob.status == S.OVERDUE:
        return 1
    if ob.due_at is not None and ob.due_at - now <= timedelta(hours=24):
        return 2
    return 3


def urgency_rank(ob: Obligation, now: datetime) -> tuple[int, int, float, int]:
    """Lower sorts first: (owed by someone else?, state group, hours until due, -priority).

    What the user must do outranks what they are merely waiting for: an overdue payment of theirs comes before a colleague's
    escalated promise, however late that is.
    """
    hours = (ob.due_at - now).total_seconds() / 3600 if ob.due_at else 1e9
    return (0 if ob.owner == "me" else 1), _urgency_group(ob, now), hours, -_PRIORITY_WEIGHT[ob.priority]


def pick_focus(items: list[Obligation], now: datetime) -> Obligation | None:
    """The one commitment that should visually dominate: the most urgent, if anything is urgent."""
    urgent = [o for o in items if _urgency_group(o, now) <= 2]
    return min(urgent, key=lambda o: urgency_rank(o, now)) if urgent else None


def headline(overdue: int, due_today: int, next_item: Obligation | None, now: datetime, tz) -> tuple[str, str | None, str]:
    """(headline, subline, tone)."""
    if overdue:
        extra = f" and {due_today} due today" if due_today else ""
        return (
            f"{overdue} overdue{extra} - start here.",
            "Complete it, or move the deadline if it changed.",
            "critical",
        )
    if due_today:
        noun = "commitment needs" if due_today == 1 else "commitments need"
        return f"{due_today} {noun} your attention today.", None, "attention"
    sub = None
    if next_item is not None and next_item.due_at is not None:
        sub = f"Next up: {next_item.title} - {fmt_when(next_item.due_at, next_item.due_precision, tz)}."
    return "Your commitments are under control.", sub or "Nothing is due today.", "calm"


def build(db: Session, user: User, now: datetime, settings: Settings) -> dict:
    tz = get_zone(user.timezone, settings.default_timezone)
    today = local_date(now, tz)
    day_start, day_end = day_bounds_utc(today, tz)
    horizon = day_bounds_utc(today + timedelta(days=8), tz)[0]

    active = list(
        db.scalars(
            select(Obligation).where(Obligation.user_id == user.id, Obligation.status.in_(list(ACTIVE_STATUSES)))
        )
    )
    overdue = sorted([o for o in active if o.status in (S.OVERDUE, S.ESCALATED)], key=lambda o: urgency_rank(o, now))
    due_today = sorted(
        [o for o in active if o.status not in (S.OVERDUE, S.ESCALATED) and o.due_at and o.due_at < day_end],
        key=lambda o: o.due_at,
    )
    appointments = sorted(
        [o for o in active if o.obligation_type in _APPOINTMENT_TYPES and o.due_at and day_start <= o.due_at < horizon],
        key=lambda o: o.due_at,
    )
    upcoming_items = sorted(
        [o for o in active if o.status not in (S.OVERDUE, S.ESCALATED) and o.due_at and day_end <= o.due_at < horizon],
        key=lambda o: o.due_at,
    )
    groups: dict[date, list[Obligation]] = {}
    for o in upcoming_items:
        groups.setdefault(local_date(o.due_at, tz), []).append(o)

    def label_for(d: date) -> str:
        if d == today + timedelta(days=1):
            return "Tomorrow"
        return f"{d:%A}, {d:%b} {d.day}"

    next_item = min([o for o in active if o.due_at and o.due_at >= day_end], key=lambda o: o.due_at, default=None)
    due_next_24h = sum(1 for o in active if o.due_at and now <= o.due_at <= now + timedelta(hours=24))
    inbox_unreviewed = (
        db.scalar(
            select(func.count()).select_from(Obligation).where(
                Obligation.user_id == user.id,
                Obligation.acknowledged_at.is_(None),
                Obligation.status.not_in([S.COMPLETED, S.DISMISSED]),
            )
        )
        or 0
    )
    approvals_pending = (
        db.scalar(
            select(func.count()).select_from(ApprovalRequest).where(
                ApprovalRequest.user_id == user.id, ApprovalRequest.status == ApprovalStatus.PENDING
            )
        )
        or 0
    )
    head, sub, tone = headline(len(overdue), len(due_today), next_item, now, tz)
    attention = len(overdue) + len(due_today)

    recent_detections = list(
        db.scalars(
            select(Obligation)
            .where(
                Obligation.user_id == user.id,
                Obligation.acknowledged_at.is_(None),
                Obligation.status.not_in([S.COMPLETED, S.DISMISSED]),
            )
            .order_by(Obligation.created_at.desc())
            .limit(5)
        )
    )
    visible_runs = (AutomationRun.user_id == user.id) | (AutomationRun.user_id.is_(None))
    runs = list(db.scalars(select(AutomationRun).where(visible_runs).order_by(AutomationRun.started_at.desc()).limit(5)))
    waiting = db.scalar(select(func.count()).select_from(AutomationRun).where(visible_runs, AutomationRun.status == RunStatus.WAITING)) or 0
    failed_24h = (
        db.scalar(
            select(func.count()).select_from(AutomationRun).where(
                visible_runs, AutomationRun.status == RunStatus.FAILED, AutomationRun.started_at >= now - timedelta(hours=24)
            )
        )
        or 0
    )
    return {
        "now": now,
        "timezone": tz.key,
        "part_of_day": part_of_day(now, tz),
        "display_name": user.display_name,
        "summary": {
            "headline": head,
            "subline": sub,
            "tone": tone,
            "attention_count": attention,
            "overdue": len(overdue),
            "due_today": len(due_today),
            "due_next_24h": due_next_24h,
            "inbox_unreviewed": inbox_unreviewed,
            "approvals_pending": approvals_pending,
        },
        "focus": pick_focus(active, now),
        "today": {"overdue": overdue, "due_today": due_today, "appointments": appointments},
        "upcoming": [{"date": d, "label": label_for(d), "items": items} for d, items in sorted(groups.items())],
        "recent_detections": recent_detections,
        "automation": {"recent_runs": [AutomationRunOut.for_viewer(r) for r in runs], "waiting": waiting, "failed_24h": failed_24h},
        "status_counts": ob_service.counts_by_status(db, user),
    }

