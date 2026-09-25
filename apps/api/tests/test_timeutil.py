"""Timezone / DST correctness. These pin behaviours that are easy to get subtly wrong."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.enums import Recurrence
from app.services.timeutil import (
    add_months,
    day_bounds_utc,
    end_of_local_day,
    get_zone,
    local_to_utc,
    next_occurrence,
    wall_time_status,
)

NY = ZoneInfo("America/New_York")
LON = ZoneInfo("Europe/London")
KOLKATA = ZoneInfo("Asia/Kolkata")


def test_ordinary_local_time_converts_using_the_zone_offset():
    assert local_to_utc(datetime(2026, 1, 15, 9, 0), NY) == datetime(2026, 1, 15, 14, 0, tzinfo=UTC)  # EST -5
    assert local_to_utc(datetime(2026, 7, 15, 9, 0), NY) == datetime(2026, 7, 15, 13, 0, tzinfo=UTC)  # EDT -4


def test_spring_forward_nonexistent_time_shifts_forward_and_is_flagged():
    naive = datetime(2026, 3, 8, 2, 30)  # clocks jump 02:00 -> 03:00 in New York
    assert wall_time_status(naive, NY) == "gap"
    assert local_to_utc(naive, NY) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # == 03:30 EDT


def test_fall_back_ambiguous_time_uses_first_occurrence_and_is_flagged():
    naive = datetime(2026, 11, 1, 1, 30)  # happens twice in New York
    assert wall_time_status(naive, NY) == "ambiguous"
    assert local_to_utc(naive, NY) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # first pass, still EDT (-4)


@pytest.mark.parametrize("naive", [datetime(2026, 3, 8, 1, 59), datetime(2026, 3, 8, 3, 0), datetime(2026, 11, 1, 2, 30)])
def test_unremarkable_times_near_dst_transitions_are_not_flagged(naive):
    assert wall_time_status(naive, NY) == "ok"


def test_half_hour_offset_zone():
    assert local_to_utc(datetime(2026, 9, 25, 17, 0), KOLKATA) == datetime(2026, 9, 25, 11, 30, tzinfo=UTC)


def test_end_of_local_day_on_a_spring_forward_day():
    # 23:59:59 EDT on Mar 8 = 03:59:59 UTC on Mar 9
    assert end_of_local_day(date(2026, 3, 8), NY) == datetime(2026, 3, 9, 3, 59, 59, tzinfo=UTC)


def test_local_days_are_23_and_25_hours_on_dst_days():
    s, e = day_bounds_utc(date(2026, 3, 8), NY)
    assert e - s == timedelta(hours=23)
    s, e = day_bounds_utc(date(2026, 11, 1), NY)
    assert e - s == timedelta(hours=25)
    s, e = day_bounds_utc(date(2026, 6, 1), NY)
    assert e - s == timedelta(hours=24)


def test_daily_recurrence_keeps_the_wall_clock_time_across_a_dst_change():
    due = local_to_utc(datetime(2026, 3, 7, 9, 0), NY)  # 09:00 EST = 14:00Z
    nxt = next_occurrence(due, Recurrence.DAILY, NY)
    assert nxt == local_to_utc(datetime(2026, 3, 8, 9, 0), NY)  # 09:00 EDT = 13:00Z, NOT 14:00Z
    assert nxt == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)


def test_weekly_recurrence_across_dst():
    due = local_to_utc(datetime(2026, 10, 30, 17, 0), NY)  # EDT
    assert next_occurrence(due, Recurrence.WEEKLY, NY) == local_to_utc(datetime(2026, 11, 6, 17, 0), NY)  # EST


@pytest.mark.parametrize(
    "start,months,expected",
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # non-leap February clamps
        (date(2028, 1, 31), 1, date(2028, 2, 29)),  # leap year
        (date(2026, 3, 31), 1, date(2026, 4, 30)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),  # year rollover
        (date(2028, 2, 29), 12, date(2029, 2, 28)),  # yearly on Feb 29
    ],
)
def test_add_months_clamps_to_month_end(start, months, expected):
    assert add_months(start, months) == expected


def test_monthly_recurrence_from_month_end():
    due = local_to_utc(datetime(2026, 1, 31, 12, 0), LON)
    nxt = next_occurrence(due, Recurrence.MONTHLY, LON)
    assert nxt == local_to_utc(datetime(2026, 2, 28, 12, 0), LON)


def test_get_zone_falls_back_instead_of_raising():
    assert get_zone("Not/AZone").key == "UTC"
    assert get_zone(None, "Asia/Tokyo").key == "Asia/Tokyo"
    assert get_zone("Bogus", "AlsoBogus").key == "UTC"
