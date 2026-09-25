"""Deterministic deadline resolution. Expected values are worked out by hand for a fixed reference:

    reference = Wed 2026-09-23 11:00 America/New_York (EDT, UTC-4)  == 2026-09-23T15:00:00Z
    date-only deadlines -> 23:59:59 local, e.g. Fri Sep 25 == 2026-09-26T03:59:59Z
"""

from __future__ import annotations

import random
import string
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.enums import DuePrecision as P
from app.services.deadlines import ResolverContext, resolve_deadline

NY = ZoneInfo("America/New_York")
REF = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
D, T = P.DATE, P.DATETIME


def ctx(**kw) -> ResolverContext:
    base = dict(reference=REF, tz=NY)
    base.update(kw)
    return ResolverContext(**base)


def z(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# (phrase, expected UTC instant, expected precision, ambiguous?)
UNAMBIGUOUS = [
    ("tomorrow", "2026-09-25T03:59:59Z", D),
    ("tomorrow at 5pm", "2026-09-24T21:00:00Z", T),
    ("by tomorrow 10:30 AM", "2026-09-24T14:30:00Z", T),
    ("today", "2026-09-24T03:59:59Z", D),
    ("tonight", "2026-09-24T03:59:59Z", D),
    ("EOD", "2026-09-23T21:00:00Z", T),
    ("COB Friday", "2026-09-25T21:00:00Z", T),
    ("by end of day tomorrow", "2026-09-24T21:00:00Z", T),
    ("Friday", "2026-09-26T03:59:59Z", D),
    ("by fri", "2026-09-26T03:59:59Z", D),
    ("by Friday 5pm", "2026-09-25T21:00:00Z", T),
    ("5:30 p.m. Friday", "2026-09-25T21:30:00Z", T),
    ("5 pm Friday", "2026-09-25T21:00:00Z", T),
    ("Fri 25 Sep", "2026-09-26T03:59:59Z", D),  # weekday agrees with the date: just decoration
    ("next Monday", "2026-09-29T03:59:59Z", D),  # nearest Monday is already in next calendar week
    ("by Wednesday", "2026-09-24T03:59:59Z", D),  # today is Wednesday, "by" makes it today
    ("this Wednesday", "2026-09-24T03:59:59Z", D),
    ("next Wednesday", "2026-10-01T03:59:59Z", D),
    ("Sep 30", "2026-10-01T03:59:59Z", D),
    ("September 30th, 2026 at 3:30 PM", "2026-09-30T19:30:00Z", T),
    ("30 Sep 2026", "2026-10-01T03:59:59Z", D),
    ("the 30th", "2026-10-01T03:59:59Z", D),
    ("the 15th", "2026-10-16T03:59:59Z", D),  # the 15th of THIS month has passed -> next month
    ("the 31st", "2026-11-01T03:59:59Z", D),  # September has 30 days -> Oct 31
    ("Sep 23", "2026-09-24T03:59:59Z", D),  # today is not "in the past" -> no rollover to next year
    ("December 25", "2026-12-26T04:59:59Z", D),  # 23:59:59 EST (UTC-5)
    ("2026-10-01", "2026-10-02T03:59:59Z", D),
    ("2026-10-01T09:00:00Z", "2026-10-01T09:00:00Z", T),
    ("2026-10-01T09:00:00-04:00", "2026-10-01T13:00:00Z", T),
    ("2026-10-01 17:00", "2026-10-01T21:00:00Z", T),
    ("10/15/2026", "2026-10-16T03:59:59Z", D),  # 15 cannot be a month
    ("15/10/2026", "2026-10-16T03:59:59Z", D),  # 15 cannot be a month
    ("10/15/26", "2026-10-16T03:59:59Z", D),  # two-digit year
    ("5.10.2026", "2026-10-06T03:59:59Z", D),  # dotted dates are day-first
    ("in 2 hours", "2026-09-23T17:00:00Z", T),
    ("in two weeks", "2026-10-08T03:59:59Z", D),
    ("within 3 days", "2026-09-27T03:59:59Z", D),
    ("in 1 month", "2026-10-24T03:59:59Z", D),
    ("in 3 business days", "2026-09-29T03:59:59Z", D),  # Thu, Fri, Mon(28)
    ("end of the week", "2026-09-25T21:00:00Z", T),
    ("EOW", "2026-09-25T21:00:00Z", T),
    ("end of the month", "2026-09-30T21:00:00Z", T),
    ("end of next week", "2026-10-02T21:00:00Z", T),
    ("noon tomorrow", "2026-09-24T16:00:00Z", T),
    ("by 5pm", "2026-09-23T21:00:00Z", T),  # 11:00 now, so 5pm is still ahead today
    ("17:00 UTC Friday", "2026-09-25T17:00:00Z", T),
    ("tomorrow 5pm PST", "2026-09-25T00:00:00Z", T),  # PDT in September: 17:00 PDT == 00:00Z
    ("9am Asia/Kolkata Friday", "2026-09-25T03:30:00Z", T),  # IST is UTC+5:30
    ("5pm UTC+5:30 on Sep 30", "2026-09-30T11:30:00Z", T),
    ("Jan 5th", "2027-01-06T04:59:59Z", D),
]


@pytest.mark.parametrize("phrase,expected,precision", UNAMBIGUOUS, ids=[c[0] for c in UNAMBIGUOUS])
def test_phrases_resolve_to_the_expected_utc_instant(phrase, expected, precision):
    r = resolve_deadline(phrase, ctx())
    assert r.due_at == z(expected), f"{phrase!r}: got {r.due_at}"
    assert r.precision == precision
    assert r.ambiguous is False, r.ambiguity
    assert r.due_at.utcoffset().total_seconds() == 0  # always UTC


AMBIGUOUS = [
    # phrase, default instant (the EARLIEST reading), all readings
    ("next Friday", "2026-09-26T03:59:59Z", ["2026-09-26T03:59:59Z", "2026-10-03T03:59:59Z"]),
    ("Wednesday", "2026-09-24T03:59:59Z", ["2026-09-24T03:59:59Z", "2026-10-01T03:59:59Z"]),
    ("next week", "2026-09-29T03:59:59Z", ["2026-09-29T03:59:59Z", "2026-10-03T03:59:59Z"]),
    ("next month", "2026-10-02T03:59:59Z", ["2026-10-02T03:59:59Z", "2026-11-01T03:59:59Z"]),
    ("Friday or Monday", "2026-09-26T03:59:59Z", ["2026-09-26T03:59:59Z", "2026-09-29T03:59:59Z"]),
    ("between Sep 25 and Sep 28", "2026-09-26T03:59:59Z", ["2026-09-26T03:59:59Z", "2026-09-29T03:59:59Z"]),
    ("Friday, September 26", "2026-09-26T03:59:59Z", ["2026-09-26T03:59:59Z", "2026-09-27T03:59:59Z"]),  # Sep 26 is a Saturday
    ("10/05/2026", "2026-10-06T03:59:59Z", ["2026-05-11T03:59:59Z", "2026-10-06T03:59:59Z"]),  # MDY default; May 10 is the other reading
]


@pytest.mark.parametrize("phrase,default,readings", AMBIGUOUS, ids=[c[0] for c in AMBIGUOUS])
def test_ambiguous_phrases_are_flagged_and_list_every_reading(phrase, default, readings):
    r = resolve_deadline(phrase, ctx())
    assert r.ambiguous is True and r.ambiguity
    assert r.due_at == z(default)
    assert sorted(r.alternatives) == sorted(z(x) for x in readings)


def test_numeric_dates_follow_the_users_configured_order_but_stay_flagged():
    mdy = resolve_deadline("10/05/2026", ctx(date_order="MDY"))
    dmy = resolve_deadline("10/05/2026", ctx(date_order="DMY"))
    assert mdy.due_at == z("2026-10-06T03:59:59Z") and dmy.due_at == z("2026-05-11T03:59:59Z")
    assert mdy.ambiguous and dmy.ambiguous
    assert sorted(mdy.alternatives) == sorted(dmy.alternatives)  # same two candidates either way


def test_both_readings_of_an_ambiguous_past_date_are_reported_as_past():
    r = resolve_deadline("03/04/2026", ctx())
    assert r.ambiguous and r.in_past and all(a < REF for a in r.alternatives)


@pytest.mark.parametrize("phrase", ["ASAP", "as soon as possible", "sometime soon", "whenever", "when you get a chance", "TBD", "urgent", "", "   ", None])
def test_vague_or_empty_phrases_yield_no_deadline_and_say_why(phrase):
    r = resolve_deadline(phrase, ctx())
    assert r.due_at is None and r.precision is None and r.method == "NONE" and r.ambiguity


def test_vague_phrase_explanation_names_the_phrase():
    assert "not a concrete deadline" in resolve_deadline("ASAP", ctx()).ambiguity
    assert "Could not find a date or time" in resolve_deadline("the usual", ctx()).ambiguity


@pytest.mark.parametrize("phrase", ["Feb 30", "February 31, 2026", "2026-02-30", "2026-13-01", "13/13/2026"])
def test_impossible_calendar_dates_are_rejected_not_rolled_over(phrase):
    r = resolve_deadline(phrase, ctx())
    assert r.due_at is None and "not a valid" in r.ambiguity


def test_a_date_without_a_year_that_already_passed_means_next_year_and_says_so():
    r = resolve_deadline("March 5", ctx())
    assert r.due_at == z("2027-03-06T04:59:59Z") and any("next year" in w for w in r.warnings)


def test_time_only_phrase_rolls_to_tomorrow_when_the_time_has_already_passed():
    late = ctx(reference=z("2026-09-23T23:00:00Z"))  # 19:00 in New York
    r = resolve_deadline("by 5pm", late)
    assert r.due_at == z("2026-09-24T21:00:00Z") and any("already passed" in w for w in r.warnings)


def test_daypart_and_midnight_are_approximations_and_say_so():
    morning = resolve_deadline("Friday morning", ctx())
    assert morning.due_at == z("2026-09-25T16:00:00Z") and any("approximate" in w for w in morning.warnings)
    midnight = resolve_deadline("midnight Friday", ctx())
    assert midnight.due_at == z("2026-09-26T03:59:59Z") and midnight.precision == D and any("midnight" in w for w in midnight.warnings)


def test_regional_timezone_abbreviations_are_flagged_and_ist_is_refused_as_ambiguous():
    est = resolve_deadline("5pm EST tomorrow", ctx())
    assert est.due_at == z("2026-09-24T21:00:00Z") and any("EST" in w for w in est.warnings)
    ist = resolve_deadline("5pm IST tomorrow", ctx())
    assert ist.due_at == z("2026-09-24T21:00:00Z") and any("IST" in w and "ambiguous" in w for w in ist.warnings)  # fell back to the user's zone


def test_the_explanation_is_human_readable_and_names_the_reference_date():
    r = resolve_deadline("by Friday 5pm", ctx())
    assert 'Read "by Friday 5pm" as Fri, Sep 25 at 5:00 PM EDT' in r.explanation
    assert "relative to the message date, Wed Sep 23" in r.explanation and "America/New_York" in r.explanation
    explicit = resolve_deadline("2026-10-01", ctx())
    assert "relative" not in explicit.explanation


# ------------------------------------------------------------------------- timezones & DST
def test_today_is_the_users_local_day_not_the_utc_day():
    tokyo = ctx(tz=ZoneInfo("Asia/Tokyo"))  # 15:00Z is already 00:00 on Thu Sep 24 in Tokyo
    r = resolve_deadline("today", tokyo)
    assert r.due_at == z("2026-09-24T14:59:59Z")
    late_ny = ctx(reference=z("2026-09-24T02:00:00Z"))  # 22:00 Wed in New York, but Thu in UTC
    assert resolve_deadline("today", late_ny).due_at == z("2026-09-24T03:59:59Z")
    assert resolve_deadline("tomorrow", late_ny).due_at == z("2026-09-25T03:59:59Z")


def test_tomorrow_across_a_dst_change_is_the_calendar_day_not_plus_24_hours():
    saturday = ctx(reference=z("2026-03-07T15:00:00Z"))  # Sat 10:00 EST; clocks go forward at 02:00 on Sunday
    r = resolve_deadline("tomorrow at 9am", saturday)
    assert r.due_at == z("2026-03-08T13:00:00Z")  # 09:00 EDT (UTC-4), not 14:00Z


def test_a_wall_time_that_does_not_exist_is_shifted_forward_with_a_warning():
    r = resolve_deadline("2026-03-08 02:30", ctx())
    assert r.due_at == z("2026-03-08T07:30:00Z") and any("does not exist" in w for w in r.warnings)


def test_a_wall_time_that_happens_twice_uses_the_first_occurrence_with_a_warning():
    r = resolve_deadline("November 1 1:30am", ctx())
    assert r.due_at == z("2026-11-01T05:30:00Z") and any("happens twice" in w for w in r.warnings)


def test_relative_dates_resolve_against_the_message_date_not_the_processing_date():
    tuesday_msg = ctx(reference=z("2026-09-22T13:00:00Z"), now=z("2026-09-25T12:00:00Z"))  # mail sent Tue, processed Fri
    r = resolve_deadline("tomorrow", tuesday_msg)
    assert r.due_at == z("2026-09-24T03:59:59Z")  # Wed Sep 23 end of day...
    assert r.in_past is True  # ...which is already history at processing time


def test_business_day_end_is_configurable():
    r = resolve_deadline("EOD", ctx(business_day_end=time(18, 30)))
    assert r.due_at == z("2026-09-23T22:30:00Z")


def test_yesterday_resolves_but_is_flagged_as_past():
    r = resolve_deadline("yesterday", ctx())
    assert r.due_at == z("2026-09-23T03:59:59Z") and r.in_past


def test_explicit_only_mode_ignores_words_that_look_like_weekdays_or_relative_dates():
    assert resolve_deadline("tomorrow", ctx(), explicit_only=True).due_at is None
    assert resolve_deadline("we sat down", ctx(), explicit_only=True).due_at is None
    assert resolve_deadline("Sep 25", ctx(), explicit_only=True).due_at == z("2026-09-26T03:59:59Z")


def test_matching_is_case_and_whitespace_insensitive_and_unicode_tolerant():
    a = resolve_deadline("  BY   FRIDAY\t5PM ", ctx())
    b = resolve_deadline("by friday 5pm", ctx())
    assert a.due_at == b.due_at
    assert resolve_deadline("by Friday – 5 pm", ctx()).due_at == b.due_at  # en dash


def test_resolution_serialises_for_storage():
    r = resolve_deadline("next Friday 5pm", ctx())
    d = r.as_dict(REF)
    assert d["method"] == "WEEKDAY" and d["precision"] == "DATETIME" and d["ambiguous"] is True
    assert d["reference_time"] == REF.isoformat() and len(d["alternatives"]) == 2


def test_the_resolver_never_raises_on_hostile_or_random_input():
    rng = random.Random(1234)
    alphabet = string.printable + "éß漢字🙂​ "
    samples = ["", "9" * 500, "/" * 200, "sep " * 100, "2026-99-99T99:99:99Z", "25:61", "0/0/0", "in 99999999999 days", "in 100000 months"]
    samples += ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 60))) for _ in range(500)]
    for text in samples:
        try:
            r = resolve_deadline(text, ctx())
        except OverflowError:
            pytest.fail(f"overflow on {text!r}")  # absurd magnitudes must degrade to 'no deadline', never crash
        assert r.due_at is None or r.due_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("phrase", ["in 99999999999 days", "in 100000 months", "in 999999 weeks", "in 9999999999 hours"])
def test_absurdly_distant_deadlines_are_refused_with_a_reason_not_a_crash(phrase):
    r = resolve_deadline(phrase, ctx())
    assert r.due_at is None and "too far away" in r.ambiguity


def test_a_deadline_ten_years_out_is_the_limit_but_five_years_is_fine():
    assert resolve_deadline("in 5 years", ctx()).due_at is None  # 'years' is not a supported unit -> no deadline, not a guess
    assert resolve_deadline("in 3650 days", ctx()).due_at is not None
    assert resolve_deadline("in 3651 days", ctx()).due_at is None


def test_explicit_only_mode_does_not_turn_a_bare_time_of_day_into_a_deadline():
    """Regression: scanning a quoted sentence for a written-out date treated 'by 5pm' as 'today at 5pm'."""
    r = resolve_deadline("Please submit your signed documents by tomorrow 5pm", ctx(), explicit_only=True)
    assert r.due_at is None and "No explicit date" in r.ambiguity
    assert resolve_deadline("send it by 5pm", ctx(), explicit_only=True).due_at is None
    assert resolve_deadline("send it by Oct 5 at 5pm", ctx(), explicit_only=True).due_at == z("2026-10-05T21:00:00Z")  # a date + time is fine


# ---- several clock times in one phrase (regression: the LAST time was used, so a 10-11am event reminded at 11am) --------------
RANGES = [
    # phrase, start of the range (the moment to show up)
    ("Friday, Sep 25, 2026 10:00 AM - 11:00 AM EDT", "2026-09-25T14:00:00Z"),
    ("Thursday Sep 24, 2026 3:00 PM - 3:30 PM (EDT)", "2026-09-24T19:00:00Z"),
    ("Sep 25 from 2pm to 4pm", "2026-09-25T18:00:00Z"),
    ("between 9am and 5pm tomorrow", "2026-09-24T13:00:00Z"),
    ("Friday 9 am until 11 am", "2026-09-25T13:00:00Z"),
    ("Friday 11pm - 1am", "2026-09-26T03:00:00Z"),  # a range that crosses midnight still starts at 11pm
]


@pytest.mark.parametrize("phrase,start", RANGES, ids=[c[0] for c in RANGES])
def test_a_time_range_resolves_to_its_start_and_is_not_treated_as_ambiguous(phrase, start):
    r = resolve_deadline(phrase, ctx())
    assert r.due_at == z(start), f"{phrase!r}: got {r.due_at}"
    assert r.ambiguous is False and "A time range was given; its start was used" in r.warnings


def test_unrelated_times_use_the_earliest_and_are_flagged_for_a_human():
    r = resolve_deadline("9am or 5pm on Friday", ctx())
    assert r.due_at == z("2026-09-25T13:00:00Z")  # earlier is the safe side: an early reminder is harmless, a late one is not
    assert r.ambiguous is True and "several times" in r.ambiguity.lower() and "earliest" in " ".join(r.warnings)


def test_the_earliest_of_several_times_is_chosen_by_time_of_day_not_by_position():
    assert resolve_deadline("5pm or 9am on Friday", ctx()).due_at == z("2026-09-25T13:00:00Z")


def test_a_single_time_is_unaffected_by_the_range_logic():
    r = resolve_deadline("Friday 5pm", ctx())
    assert r.due_at == z("2026-09-25T21:00:00Z") and not r.ambiguous and not r.warnings


def test_the_same_time_written_twice_is_not_ambiguous():
    r = resolve_deadline("Friday 5pm (that is 17:00)", ctx())
    assert r.due_at == z("2026-09-25T21:00:00Z") and r.ambiguous is False
