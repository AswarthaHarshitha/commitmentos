"""Reminder policy: turns a deadline into a *ladder* of timed rungs.

Pure functions only. Given (due_at, precision, policy) they answer "when should the system
act?" - the monitor then decides what to do with the answer.

Default ladder for a deadline D (configurable):

    D-24h  REMINDER                normal reminder
    D-6h   HIGH_PRIORITY_REMINDER  high-priority reminder (the last offset)
    D      OVERDUE                 mark overdue + notify
    D+24h  ESCALATION              escalate + notify

Date-only deadlines (no time given) are stored as 23:59:59 local on that day, but reminder
offsets are anchored to *business-day end* on that day so nobody is pinged at midnight.
Overdue still only begins once the local day has actually ended.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.config import Settings
from app.enums import DuePrecision, NotificationKind
from app.models import User
from app.schemas.user import UserPreferences
from app.services.timeutil import get_zone, local_time_on, to_local


@dataclass(frozen=True)
class ReminderPolicy:
    offsets_hours: tuple[int, ...]  # descending
    escalate_after_hours: int
    business_day_end: time
    tz: ZoneInfo
    notify_email: bool
    notify_telegram: bool
    telegram_chat_id: str | None


def preferences_of(user: User) -> UserPreferences:
    try:
        return UserPreferences.model_validate(user.preferences or {})
    except ValidationError:
        return UserPreferences()  # corrupt prefs must never break the monitor


def policy_for(user: User, settings: Settings) -> ReminderPolicy:
    prefs = preferences_of(user)
    bde = time.fromisoformat(prefs.business_day_end) if prefs.business_day_end else settings.business_day_end_time
    return ReminderPolicy(
        offsets_hours=tuple(prefs.reminder_offsets_hours or settings.reminder_offsets_hours),
        escalate_after_hours=prefs.escalate_after_hours or settings.escalate_after_hours,
        business_day_end=bde,
        tz=get_zone(user.timezone, settings.default_timezone),
        notify_email=prefs.notify_email,
        notify_telegram=prefs.notify_telegram and bool(prefs.telegram_chat_id),
        telegram_chat_id=prefs.telegram_chat_id,
    )


@dataclass(frozen=True)
class Rung:
    key: str
    at: datetime
    kind: NotificationKind


def reminder_anchor(due_at: datetime, precision: DuePrecision | None, policy: ReminderPolicy) -> datetime:
    if precision == DuePrecision.DATE:
        local_day = to_local(due_at, policy.tz).date()
        return local_time_on(local_day, policy.business_day_end, policy.tz)
    return due_at


def build_ladder(due_at: datetime, precision: DuePrecision | None, policy: ReminderPolicy) -> list[Rung]:
    anchor = reminder_anchor(due_at, precision, policy)
    offsets = sorted(policy.offsets_hours, reverse=True)
    rungs: list[Rung] = []
    for index, hours in enumerate(offsets):
        is_last = index == len(offsets) - 1 and len(offsets) > 1
        kind = NotificationKind.HIGH_PRIORITY_REMINDER if is_last else NotificationKind.REMINDER
        rungs.append(Rung(f"T-{hours}h", anchor - timedelta(hours=hours), kind))
    rungs.append(Rung("OVERDUE", due_at, NotificationKind.OVERDUE))
    rungs.append(Rung("ESCALATED", due_at + timedelta(hours=policy.escalate_after_hours), NotificationKind.ESCALATION))
    return sorted(rungs, key=lambda r: r.at)


def due_rungs(ladder: list[Rung], now: datetime) -> list[Rung]:
    return [r for r in ladder if r.at <= now]


def next_rung_after(ladder: list[Rung], now: datetime) -> Rung | None:
    return next((r for r in ladder if r.at > now), None)
