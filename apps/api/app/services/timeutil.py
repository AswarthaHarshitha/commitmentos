"""Timezone-safe primitives shared by the deadline resolver, reminder policy and recurrence.

Rule of thumb: do calendar arithmetic on *local dates/wall-clock times*, then convert to UTC
once at the end. Never add ``timedelta(days=1)`` to an aware datetime to mean "tomorrow": on a
DST-change day that is 23 or 25 hours, not "the same wall-clock time tomorrow".
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.enums import DuePrecision, Recurrence


def get_zone(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    for candidate in (name, fallback, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed here; attach a timezone first")
    return dt.astimezone(UTC)


def local_to_utc(naive_local: datetime, tz: ZoneInfo) -> datetime:
    """Interpret a naive wall-clock time in ``tz`` and return the UTC instant.

    * Non-existent time (spring-forward gap, e.g. 02:30 on 2026-03-08 in New York): resolves to
      the instant *after* the gap (03:30 EDT), i.e. shifted forward by the gap.
    * Ambiguous time (fall-back overlap, e.g. 01:30 on 2026-11-01): resolves to the *first*
      occurrence (still daylight time).
    Both follow from PEP 495 ``fold=0``; the tests pin this behaviour.
    """
    if naive_local.tzinfo is not None:
        raise ValueError("expected a naive datetime")
    return naive_local.replace(tzinfo=tz, fold=0).astimezone(UTC)


def wall_time_status(naive_local: datetime, tz: ZoneInfo) -> str:
    """'ok' | 'gap' (does not exist) | 'ambiguous' (occurs twice) - used to surface DST warnings."""
    first = naive_local.replace(tzinfo=tz, fold=0)
    second = naive_local.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second.utcoffset():
        return "ok"
    # offsets differ: either a gap or an overlap
    round_trip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    return "gap" if round_trip != naive_local else "ambiguous"


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return ensure_aware_utc(dt).astimezone(tz)


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return to_local(dt, tz).date()


def end_of_local_day(d: date, tz: ZoneInfo) -> datetime:
    """23:59:59 local time on ``d`` as a UTC instant (the deadline for date-only obligations)."""
    return local_to_utc(datetime.combine(d, time(23, 59, 59)), tz)


def local_time_on(d: date, t: time, tz: ZoneInfo) -> datetime:
    return local_to_utc(datetime.combine(d, t), tz)


def start_of_local_day(d: date, tz: ZoneInfo) -> datetime:
    return local_to_utc(datetime.combine(d, time(0, 0)), tz)


def day_bounds_utc(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) of the local calendar day ``d`` as UTC instants (handles 23/25-hour days)."""
    return start_of_local_day(d, tz), start_of_local_day(d + timedelta(days=1), tz)


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_occurrence(due_at: datetime, recurrence: Recurrence, tz: ZoneInfo) -> datetime:
    """Next due instant for a recurring obligation, keeping the local wall-clock time."""
    local = to_local(due_at, tz)
    d = local.date()
    if recurrence == Recurrence.DAILY:
        nd = d + timedelta(days=1)
    elif recurrence == Recurrence.WEEKLY:
        nd = d + timedelta(weeks=1)
    elif recurrence == Recurrence.MONTHLY:
        nd = add_months(d, 1)
    else:
        nd = add_months(d, 12)
    return local_to_utc(datetime.combine(nd, local.time().replace(tzinfo=None)), tz)


def fmt_when(dt: datetime, precision: DuePrecision | None, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    day = f"{local:%a}, {local:%b} {local.day}"
    if precision == DuePrecision.DATE:
        return day
    hour = local.hour % 12 or 12
    return f"{day} at {hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} {local.tzname()}"


def fmt_span(delta: timedelta) -> str:
    seconds = abs(int(delta.total_seconds()))
    if seconds < 90:
        return "less than a minute"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes"
    hours = round(seconds / 3600)
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"{days} days"
