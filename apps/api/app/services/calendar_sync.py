"""Calendar Sync: which commitments deserve a calendar event, and what that event should look like.

A rule set, not a model. The workflow only ever *proposes* (a PENDING approval); nothing is created in any calendar
until the user approves - "ask, then create". Once any proposal exists for an obligation (pending, approved, executed,
rejected or expired) it is never proposed again, so a "no" is respected and a scan can run as often as it likes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ApprovalAction, DuePrecision, ObligationType
from app.enums import ObligationStatus as S
from app.models import ApprovalRequest, CalendarEvent, Obligation, User
from app.services.timeutil import fmt_when, get_zone, local_date, local_time_on

APPOINTMENT_TYPES = frozenset({ObligationType.APPOINTMENT, ObligationType.INTERVIEW})
APPOINTMENT_LENGTH = {ObligationType.INTERVIEW: timedelta(hours=1)}
DEFAULT_APPOINTMENT_LENGTH = timedelta(minutes=30)
DEADLINE_BLOCK = timedelta(minutes=30)
MIN_LEAD = timedelta(hours=2)  # a calendar entry for something due within the hour helps nobody
_ELIGIBLE = (S.OPEN, S.ACTION_REQUIRED)  # accepted and still ahead of us (SCHEDULED already has its event)


@dataclass(frozen=True)
class CalendarProposal:
    obligation_id: str
    user_email: str
    title: str
    rationale: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "user_email": self.user_email,
            "action_type": ApprovalAction.CREATE_CALENDAR_EVENT.value,
            "title": self.title,
            "rationale": self.rationale,
            "payload": self.payload,
        }


def event_window(ob: Obligation, tz: ZoneInfo, settings: Settings, now: datetime) -> tuple[datetime, datetime, str] | None:
    """(start, end, title) for the event, or None if it would be pointless (already past / imminent).

    An appointment or interview with a clock time starts at that time. Anything else is a *deadline*: a short block that
    ends at the deadline (for a date-only deadline, at the end of that business day).
    """
    assert ob.due_at is not None
    if ob.obligation_type in APPOINTMENT_TYPES and ob.due_precision == DuePrecision.DATETIME:
        start = ob.due_at
        end = start + APPOINTMENT_LENGTH.get(ob.obligation_type, DEFAULT_APPOINTMENT_LENGTH)
        title, lead_from = ob.title, start
    else:
        anchor = ob.due_at
        if ob.due_precision == DuePrecision.DATE:
            office_close = local_time_on(local_date(ob.due_at, tz), settings.business_day_end_time, tz)
            anchor = office_close if office_close > now else ob.due_at
        start, end = anchor - DEADLINE_BLOCK, anchor
        title, lead_from = f"Due: {ob.title}", anchor
    if lead_from - now < MIN_LEAD:
        return None
    return start, end, title[:300]


def scan(db: Session, now: datetime, settings: Settings, limit: int = 25) -> list[CalendarProposal]:
    has_event = exists().where(CalendarEvent.obligation_id == Obligation.id)
    was_proposed = exists().where(
        ApprovalRequest.obligation_id == Obligation.id, ApprovalRequest.action_type == ApprovalAction.CREATE_CALENDAR_EVENT
    )
    rows = db.execute(
        select(Obligation, User)
        .join(User, User.id == Obligation.user_id)
        .where(Obligation.status.in_(_ELIGIBLE), Obligation.due_at.is_not(None), Obligation.due_at > now, ~has_event, ~was_proposed)
        .order_by(Obligation.due_at)
        .limit(limit)
    ).all()

    proposals: list[CalendarProposal] = []
    base = settings.public_web_url.rstrip("/")
    for ob, user in rows:
        tz = get_zone(user.timezone, settings.default_timezone)
        window = event_window(ob, tz, settings, now)
        if window is None:
            continue
        start, end, title = window
        assert ob.due_at is not None
        proposals.append(
            CalendarProposal(
                obligation_id=str(ob.id),
                user_email=user.email,
                title=f"Add to your calendar: {ob.title}"[:300],
                rationale=(
                    f"\"{ob.title}\" is due {fmt_when(ob.due_at, ob.due_precision, tz)}. "
                    "An event keeps it visible next to your other plans. Nothing is added until you approve."
                ),
                payload={
                    "title": title,
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "timezone": tz.key,
                    "description": f"Added by CommitmentOS for a commitment you are tracking. Open it: {base}/obligations/{ob.id}",
                },
            )
        )
    return proposals
