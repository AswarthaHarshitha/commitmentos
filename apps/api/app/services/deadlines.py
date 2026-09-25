"""Deterministic deadline resolution: a *phrase* in, a UTC instant out. No LLM involved.

The LLM only copies the phrase the sender wrote ("by Friday 5pm"). This module turns it into a
timestamp, relative to **when the message was received** (not when we processed it), in the
**user's timezone**, with explicit DST handling. Design rules, all pinned by tests:

* Date-only deadlines become the *end of that local day* (precision DATE).
* "EOD", "COB", "end of business" mean the configured business-day end (default 17:00).
* Ambiguity is never guessed silently. Each ambiguous reading is returned as an alternative, the
  result is flagged ``ambiguous``, and the default is the EARLIEST reading - a reminder that fires a
  day too early is harmless, one that fires a day too late is not. (Numeric dates like 03/04 are the one
  exception: they follow the user's configured date order, and are still flagged.)
* Things we cannot resolve ("soon", "ASAP") yield no deadline and say why.
* Nothing is ever resolved by asking the LLM to do date maths.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo, available_timezones

from app.enums import DuePrecision
from app.services.timeutil import add_months, fmt_when, to_local, wall_time_status

DateOrder = Literal["MDY", "DMY"]


@dataclass(frozen=True)
class ResolverContext:
    reference: datetime  # aware; when the message was received
    tz: ZoneInfo  # the user's timezone
    business_day_end: time = time(17, 0)
    date_order: DateOrder = "MDY"
    now: datetime | None = None  # only used to flag deadlines that are already in the past


@dataclass
class Resolution:
    due_at: datetime | None
    precision: DuePrecision | None
    method: str  # EXPLICIT_DATE | RELATIVE | WEEKDAY | BUSINESS_TERM | NONE
    matched_text: str | None
    timezone: str
    ambiguous: bool = False
    ambiguity: str | None = None
    alternatives: list[datetime] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explanation: str | None = None
    in_past: bool = False
    llm_suggestion: str | None = None  # a date the AI proposed that the wording did not support (never applied)

    def as_dict(self, reference: datetime | None = None) -> dict[str, Any]:
        return {
            "method": self.method,
            "matched_text": self.matched_text,
            "timezone": self.timezone,
            "precision": self.precision.value if self.precision else None,
            "reference_time": reference.isoformat() if reference else None,
            "explanation": self.explanation,
            "ambiguous": self.ambiguous,
            "alternatives": [a.isoformat() for a in self.alternatives],
            "warnings": list(self.warnings),
            "in_past": self.in_past,
            "llm_suggestion": self.llm_suggestion,
        }


# ---------------------------------------------------------------------------------------- vocabulary
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
    "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_NUMWORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_TZ_ABBR = {
    "utc": "UTC", "gmt": "UTC", "z": "UTC", "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago", "mt": "America/Denver",
    "mst": "America/Denver", "mdt": "America/Denver", "pt": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles", "bst": "Europe/London", "cet": "Europe/Paris", "cest": "Europe/Paris",
}
_VAGUE = re.compile(
    r"\b(asap|as soon as (?:possible|you can)|immediately|urgent(?:ly)?|right away|soon|whenever|"
    r"when you (?:can|get a chance)|at your earliest convenience|no rush|tbd|tba|eventually)\b"
)

_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_WD = r"(?P<wd>monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)"
_NUMW = "|".join(sorted(_NUMWORDS, key=len, reverse=True))
_UNIT = r"(?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?|months?)"
_TZTOK = r"(?:\s*(?P<tz>utc|gmt|z|[ecmp][sd]?t|bst|cest?|ist)(?![a-z]))?"

_RE_ISO = re.compile(
    r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:[t\s]+(?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.\d+)?)?)?\s*(?P<off>z|[+-]\d{2}:?\d{2})?(?![\w])"
)
_RE_UTC_OFFSET = re.compile(r"\b(?:utc|gmt)\s*(?P<sign>[+-])\s*(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?\b")
_RE_IANA = re.compile(r"\b(?P<name>[a-z]+/[a-z_]+(?:/[a-z_]+)?)\b")
_RE_BUSINESS_TIME = re.compile(
    r"\b(?:eod|e\.o\.d\.?|end of (?:the )?(?:business )?day|end of business(?: day)?|cob|c\.o\.b\.?|close of business|eob)\b"
)
_RE_NOON = re.compile(r"\b(?P<w>noon|midday|midnight)\b")
_RE_AMPM = re.compile(rf"\b(?P<h>\d{{1,2}})(?::(?P<m>\d{{2}}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z]){_TZTOK}")
_RE_24H = re.compile(rf"(?<![\d:/.-])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?::[0-5]\d)?(?!\s*(?:a\.?m|p\.?m))(?![\d:]){_TZTOK}")
_RE_DAYPART = re.compile(r"\b(?P<w>morning|afternoon|evening)\b")
_RE_MD = re.compile(rf"\b{_MON}\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<y>20\d{{2}}))?\b")
_RE_DM = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\.?(?:\s*,?\s*(?P<y>20\d{{2}}))?\b")
_RE_NUMERIC = re.compile(r"\b(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?:(?P=sep)(?P<y>\d{4}|\d{2}))?(?![\d:])")
_RE_ORDINAL = re.compile(r"\bthe\s+(?P<d>\d{1,2})(?:st|nd|rd|th)\b")
_RE_REL_DAY = re.compile(r"\b(?P<w>today|tonight|tomorrow|tmrw|day after tomorrow|yesterday)\b")
_RE_IN_UNITS = re.compile(
    rf"\b(?:in|within|after)\s+(?:about\s+|around\s+|the next\s+)?(?P<n>\d+|{_NUMW})\s+(?:(?P<biz>business|working)\s+)?{_UNIT}\b"
)
_RE_FROM_NOW = re.compile(rf"\b(?P<n>\d+|{_NUMW})\s+(?:(?P<biz>business|working)\s+)?{_UNIT}\s+from\s+(?:now|today)\b")
_RE_PERIOD = re.compile(
    r"\b(?P<k>end of next week|end of next month|next week|next month|end of (?:the |this )?week|this week|eow|"
    r"end of (?:the |this )?month|this month|eom)\b"
)
_RE_WEEKDAY = re.compile(rf"\b(?:(?P<mod>this|next|coming|upcoming|following)\s+)?{_WD}\b")
_RE_PREP = re.compile(r"\b(?:by|before|until|till|no later than|not later than|due)\b")


# Anything further out than ~10 years is a typo or an attack, not a deadline.
_MAX_UNITS = {"minute": 5_256_000, "min": 5_256_000, "hour": 87_600, "hr": 87_600, "day": 3_650, "week": 520, "month": 120}


class _Implausible(Exception):
    """The phrase parses but describes something too far away to be a real deadline."""


@lru_cache(maxsize=1)
def _iana_lookup() -> dict[str, str]:
    return {name.lower(): name for name in available_timezones()}


# ---------------------------------------------------------------------------------------- internals
@dataclass
class _Cand:
    kind: str  # iso | explicit | numeric | ordinal | relday | units | period | weekday
    d: date | None = None
    moment: datetime | None = None  # exact instant (ISO with offset, "in 2 hours")
    time_hint: time | None = None  # implied clock time (e.g. end-of-week -> business end)
    alts: list[date] = field(default_factory=list)
    ambiguity: str | None = None
    note: str | None = None
    precision_hint: DuePrecision | None = None


@dataclass
class _Clock:
    kind: str  # clock | business | noon | midnight | daypart
    value: time | None
    tz: tzinfo | None = None
    warning: str | None = None
    in_range: bool = False  # one end of "10am - 11am" / "from 2pm to 4pm" / "between 9am and 5pm"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def _blank(s: str, m: re.Match[str]) -> str:
    return s[: m.start()] + " " * (m.end() - m.start()) + s[m.end():]


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _tz_from_token(tok: str | None, warnings: list[str]) -> tzinfo | None:
    if not tok:
        return None
    if tok == "ist":
        warnings.append("'IST' is ambiguous (India / Ireland / Israel); your own timezone was used instead")
        return None
    name = _TZ_ABBR.get(tok)
    if name is None:
        return None
    if tok in ("est", "edt", "et", "cst", "cdt", "ct", "mst", "mdt", "mt", "pst", "pdt", "pt"):
        warnings.append(f"'{tok.upper()}' was read as the regional timezone {name} (daylight saving aware)")
    return ZoneInfo(name)


def _extract_timezone(s: str, warnings: list[str]) -> tuple[tzinfo | None, str]:
    m = _RE_UTC_OFFSET.search(s)
    if m:
        minutes = int(m["h"]) * 60 + int(m["m"] or 0)
        offset = timedelta(minutes=minutes if m["sign"] == "+" else -minutes)
        return timezone(offset), _blank(s, m)
    for m in _RE_IANA.finditer(s):
        canonical = _iana_lookup().get(m["name"])
        if canonical:
            return ZoneInfo(canonical), _blank(s, m)
    return None, s


_RANGE_CONNECTOR = re.compile(r"\s*(?:-|to|until|till|through|thru|and)\s*")


def _extract_clocks(s: str, warnings: list[str]) -> tuple[list[_Clock], str]:
    original = s  # blanking keeps every offset stable, so spans found below index into this copy too
    found: list[tuple[int, int, _Clock]] = []
    for m in _RE_BUSINESS_TIME.finditer(s):
        found.append((m.start(), m.end(), _Clock("business", None)))
        s = _blank(s, m)
    for m in _RE_NOON.finditer(s):
        word = m["w"]
        found.append((m.start(), m.end(), _Clock("midnight", None) if word == "midnight" else _Clock("noon", time(12, 0))))
        s = _blank(s, m)
    for m in list(_RE_AMPM.finditer(s)):
        hour, minute = int(m["h"]), int(m["m"] or 0)
        if not 1 <= hour <= 12 or minute > 59:
            continue
        pm = m["ap"].startswith("p")
        hour = (hour % 12) + (12 if pm else 0)
        found.append((m.start(), m.end(), _Clock("clock", time(hour, minute), _tz_from_token(m["tz"], warnings))))
        s = _blank(s, m)
    for m in list(_RE_24H.finditer(s)):
        found.append((m.start(), m.end(), _Clock("clock", time(int(m["h"]), int(m["m"])), _tz_from_token(m["tz"], warnings))))
        s = _blank(s, m)
    for m in _RE_DAYPART.finditer(s):
        word = m["w"]
        value, label = {"morning": (time(12, 0), "12:00"), "afternoon": (time(17, 0), "17:00"), "evening": (time(21, 0), "21:00")}[word]
        found.append((m.start(), m.end(), _Clock("daypart", value, None, f"'{word}' is approximate; treated as {label}")))
        s = _blank(s, m)
    found.sort(key=lambda x: x[0])
    for (_, end_a, a), (start_b, _, b) in zip(found, found[1:], strict=False):
        if _RANGE_CONNECTOR.fullmatch(original[end_a:start_b]):
            a.in_range = b.in_range = True
    return [c for _, _, c in found], s


def _pick_clock(clocks: list[_Clock], business_day_end: time) -> tuple[_Clock | None, str | None]:
    """The clock to use, and how it was chosen: None (only one), "range" or "several".

    Several distinct times: the EARLIEST is used - a reminder that arrives early is harmless, one that arrives
    after the fact is not. A plain start-end range ("10:00 AM - 11:00 AM": an event) is not ambiguous, its start
    is when to show up; any other mix of times is flagged for a human.
    """
    if not clocks:
        return None, None
    if len({(c.kind, c.value) for c in clocks}) == 1:
        return clocks[-1], None

    def time_of_day(c: _Clock) -> time:
        return c.value or (business_day_end if c.kind == "business" else time(23, 59, 59))

    if len(clocks) == 2 and all(c.in_range for c in clocks):
        return clocks[0], "range"  # the START, even when the range crosses midnight ("11pm - 1am")
    return min(clocks, key=time_of_day), "several"


def _year_for_month_day(month: int, day: int, today: date, warnings: list[str]) -> date | None:
    """A date with no year: the next occurrence on or after today."""
    candidate = _safe_date(today.year, month, day)
    if candidate is None:
        return None
    if candidate < today:
        nxt = _safe_date(today.year + 1, month, day)
        if nxt is None:
            return None
        warnings.append(f"No year given; {candidate:%b} {day} has passed this year so next year was assumed")
        return nxt
    return candidate


def _ordinal_day(day: int, today: date) -> date | None:
    for offset in range(0, 13):
        base = add_months(today.replace(day=1), offset)
        candidate = _safe_date(base.year, base.month, day)
        if candidate is not None and candidate >= today:
            return candidate
    return None


def _add_business_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if d.weekday() < 5:
            remaining -= 1
    return d


def _weekday_candidate(wd: int, mod: str | None, today: date, has_prep: bool) -> _Cand:
    delta = (wd - today.weekday()) % 7
    name = calendar.day_name[wd]
    if mod in ("next", "following"):
        nearest = today + timedelta(days=delta or 7)
        if nearest.isocalendar()[:2] == today.isocalendar()[:2]:
            later = nearest + timedelta(days=7)
            return _Cand(
                "weekday", nearest, alts=[nearest, later],
                ambiguity=f"'next {name}' can mean {nearest:%a %b} {nearest.day} (this coming {name}) or {later:%a %b} {later.day} (the {name} after)",
            )
        return _Cand("weekday", nearest)
    if delta == 0:
        if not has_prep and mod is None:
            later = today + timedelta(days=7)
            return _Cand(
                "weekday", today, alts=[today, later],
                ambiguity=f"Today is already {name}: the message may mean today or next {name} ({later:%b} {later.day})",
            )
        return _Cand("weekday", today)
    return _Cand("weekday", today + timedelta(days=delta))


def _to_utc(naive: datetime, tz: tzinfo) -> datetime:
    if isinstance(tz, ZoneInfo):
        return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)
    return naive.replace(tzinfo=tz).astimezone(UTC)


def _end_of_day(d: date, tz: tzinfo) -> datetime:
    return _to_utc(datetime.combine(d, time(23, 59, 59)), tz)


def _unresolved(ctx: ResolverContext, raw: str | None, reason: str, warnings: list[str] | None = None) -> Resolution:
    return Resolution(
        due_at=None, precision=None, method="NONE", matched_text=raw, timezone=ctx.tz.key,
        ambiguous=False, ambiguity=reason, warnings=warnings or [], explanation=reason,
    )


# ---------------------------------------------------------------------------------------- public API
def resolve_deadline(text: str | None, ctx: ResolverContext, *, explicit_only: bool = False) -> Resolution:
    """Resolve a deadline phrase. Never raises: anything that cannot be interpreted becomes "no deadline" with a reason.

    Input comes from untrusted email text, so this is a hard boundary."""
    try:
        return _resolve(text, ctx, explicit_only)
    except _Implausible as exc:
        return _unresolved(ctx, (text or "").strip() or None, str(exc))
    except (OverflowError, ValueError, KeyError, IndexError):  # defensive: a parsing bug must never fail an extraction
        return _unresolved(ctx, (text or "").strip() or None, "The deadline phrase could not be interpreted.")


def _resolve(text: str | None, ctx: ResolverContext, explicit_only: bool) -> Resolution:
    """Implementation of ``resolve_deadline``. ``explicit_only`` limits parsing to written-out dates (ISO, "Sep 25",
    "25/09/2026"), used when scanning free text where words like "sat"/"sun" must not be read as weekdays."""
    raw = (text or "").strip()
    s = _norm(raw)
    if not s:
        return _unresolved(ctx, None, "No deadline phrase was found in the message.")

    warnings: list[str] = []
    ref_local = to_local(ctx.reference, ctx.tz)
    today = ref_local.date()
    has_prep = bool(_RE_PREP.search(s))
    cands: list[_Cand] = []

    tz_override, s = _extract_timezone(s, warnings)

    # ISO 8601 (the one form that may carry its own offset)
    for m in list(_RE_ISO.finditer(s)):
        d = _safe_date(int(m["y"]), int(m["m"]), int(m["d"]))
        if d is None:
            return _unresolved(ctx, raw, f"'{m.group(0).strip()}' is not a valid calendar date.", warnings)
        c = _Cand("iso", d)
        if m["h"] is not None:
            naive = datetime(d.year, d.month, d.day, int(m["h"]), int(m["mi"]), int(m["s"] or 0))
            off = m["off"]
            if off:
                if off == "z":
                    c.moment = naive.replace(tzinfo=UTC)
                else:
                    sign = 1 if off[0] == "+" else -1
                    digits = off[1:].replace(":", "")
                    c.moment = naive.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))))
                c.moment = c.moment.astimezone(UTC)
            else:
                c.time_hint = naive.time()
        cands.append(c)
        s = _blank(s, m)

    clocks, s = _extract_clocks(s, warnings)

    # written-out dates
    for m in list(_RE_MD.finditer(s)) + list(_RE_DM.finditer(s)):
        month = _MONTHS[m["mon"]]
        day = int(m["d"])
        if m["y"]:
            d = _safe_date(int(m["y"]), month, day)
        else:
            d = _year_for_month_day(month, day, today, warnings)
        if d is None:
            return _unresolved(ctx, raw, f"'{m.group(0).strip()}' is not a valid calendar date.", warnings)
        cands.append(_Cand("explicit", d))
    s = _RE_MD.sub(" ", s)
    s = _RE_DM.sub(" ", s)

    for m in _RE_NUMERIC.finditer(s):
        a, b, sep, y = int(m["a"]), int(m["b"]), m["sep"], m["y"]
        if sep in ".-" and not y:
            continue  # "5.30" / "9-5" are times/ranges, not dates
        year = None if not y else (int(y) if len(y) == 4 else 2000 + int(y))
        readings: list[tuple[int, int]] = []  # (month, day)
        if sep == ".":
            readings = [(b, a)]  # dotted dates are day-first (European)
        elif a > 12 and b <= 12:
            readings = [(b, a)]
        elif b > 12 and a <= 12:
            readings = [(a, b)]
        elif a == b:
            readings = [(a, b)]
        else:
            mdy, dmy = (a, b), (b, a)
            readings = [mdy, dmy] if ctx.date_order == "MDY" else [dmy, mdy]
        resolved: list[date] = []
        for month, day in readings:
            if year is not None:
                d = _safe_date(year, month, day)
            else:
                d = _year_for_month_day(month, day, today, warnings)
            if d is not None:
                resolved.append(d)
        if not resolved:
            return _unresolved(ctx, raw, f"'{m.group(0)}' is not a valid calendar date.", warnings)
        c = _Cand("numeric", resolved[0])
        if len(resolved) > 1 and resolved[0] != resolved[1]:
            c.alts = sorted(resolved)
            c.ambiguity = (
                f"'{m.group(0)}' can be read as {resolved[0]:%b} {resolved[0].day} or {resolved[1]:%b} {resolved[1].day} "
                f"(month/day order differs between senders); read as {ctx.date_order}"
            )
        cands.append(c)
    s = _RE_NUMERIC.sub(" ", s)

    if not explicit_only:
        for m in list(_RE_ORDINAL.finditer(s)):
            d = _ordinal_day(int(m["d"]), today)
            if d is None:
                return _unresolved(ctx, raw, f"'{m.group(0)}' is not a valid day of the month.", warnings)
            cands.append(_Cand("ordinal", d))
        s = _RE_ORDINAL.sub(" ", s)

        for m in _RE_IN_UNITS.finditer(s):
            cands.append(_units_candidate(m, ctx, ref_local, today))
        s = _RE_IN_UNITS.sub(" ", s)
        for m in _RE_FROM_NOW.finditer(s):
            cands.append(_units_candidate(m, ctx, ref_local, today))
        s = _RE_FROM_NOW.sub(" ", s)

        for m in _RE_PERIOD.finditer(s):
            cands.append(_period_candidate(m["k"], ctx, today))
        s = _RE_PERIOD.sub(" ", s)

        for m in _RE_REL_DAY.finditer(s):
            word = m["w"]
            offset = {"today": 0, "tonight": 0, "tomorrow": 1, "tmrw": 1, "day after tomorrow": 2, "yesterday": -1}[word]
            c = _Cand("relday", today + timedelta(days=offset))
            if word == "tonight":
                c.note = "tonight"
            cands.append(c)
        s = _RE_REL_DAY.sub(" ", s)

        weekday_cands = [
            (_weekday_candidate(_WEEKDAYS[m["wd"]], m["mod"], today, has_prep), _WEEKDAYS[m["wd"]])
            for m in _RE_WEEKDAY.finditer(s)
        ]
        s = _RE_WEEKDAY.sub(" ", s)
        # A weekday next to an explicit date is decoration - but only if it agrees with the date.
        explicit_dates = [c for c in cands if c.kind in ("iso", "explicit", "numeric") and c.d]
        for wc, wd_index in weekday_cands:
            if explicit_dates:
                anchor = explicit_dates[0]
                if anchor.d.weekday() != wd_index:  # type: ignore[union-attr]
                    nearest = today + timedelta(days=(wd_index - today.weekday()) % 7)
                    anchor.alts = sorted({anchor.d, nearest})  # type: ignore[arg-type]
                    anchor.ambiguity = (
                        f"The message says {calendar.day_name[wd_index]} but {anchor.d:%b} {anchor.d.day} is a "  # type: ignore[union-attr]
                        f"{calendar.day_name[anchor.d.weekday()]}"  # type: ignore[union-attr]
                    )
                    if anchor.moment is None:
                        anchor.d = anchor.alts[0]  # earliest reading is the safe default
                continue
            cands.append(wc)

    # ---- choose one date candidate ------------------------------------------------------------
    ambiguous = False
    ambiguity: str | None = None
    alternatives: list[date] = []
    chosen: _Cand | None = None
    dated = [c for c in cands if c.d or c.moment]
    if dated:
        if len({(c.moment or c.d) for c in dated}) > 1:
            def instant(c: _Cand) -> datetime:
                return c.moment if c.moment is not None else _end_of_day(c.d, tz_override or ctx.tz)  # type: ignore[arg-type]

            dated.sort(key=instant)
            chosen = dated[0]
            ambiguous = True
            ambiguity = "The message mentions several dates; the earliest was used"
            alternatives = [c.d for c in dated if c.d]
        else:
            chosen = dated[0]
        if chosen.ambiguity:
            ambiguous = True
            ambiguity = f"{ambiguity}; {chosen.ambiguity}" if ambiguity else chosen.ambiguity
            alternatives = sorted(set(alternatives) | set(chosen.alts))

    tz = tz_override or next((c.tz for c in clocks if c.tz), None) or ctx.tz
    clock, how = _pick_clock(clocks, ctx.business_day_end)
    if how == "range":
        warnings.append("A time range was given; its start was used")
    elif how == "several":
        warnings.append("Several times were found; the earliest was used")
        ambiguous = True
        ambiguity = f"{ambiguity}; several times were mentioned" if ambiguity else "The message mentions several times; the earliest was used"
    if clock and clock.warning:
        warnings.append(clock.warning)

    if chosen is None and explicit_only:
        # Scanning free text for *written-out* dates: a bare time of day ("by 5pm") is not a deadline here.
        return _unresolved(ctx, raw, "No explicit date was found.", warnings)

    if chosen is None and clock is None:
        if _VAGUE.search(_norm(raw)):
            return _unresolved(ctx, raw, f"'{raw}' is not a concrete deadline.", warnings)
        return _unresolved(ctx, raw, f"Could not find a date or time in '{raw}'.", warnings)

    # ---- assemble the instant -----------------------------------------------------------------
    precision = DuePrecision.DATETIME
    method = "EXPLICIT_DATE"
    if chosen is None:  # time only: "by 5pm"
        assert clock is not None
        method = "BUSINESS_TERM" if clock.kind == "business" else "RELATIVE"
        base = today
        wall = ctx.business_day_end if clock.kind == "business" else (clock.value or time(23, 59, 59))
        if clock.kind == "midnight":
            due = _end_of_day(today, tz)
            precision = DuePrecision.DATE
            warnings.append("'midnight' was read as the end of that day")
        else:
            due = _to_utc(datetime.combine(base, wall), tz)
            if clock.kind != "business" and due <= ctx.reference:
                base = today + timedelta(days=1)
                due = _to_utc(datetime.combine(base, wall), tz)
                warnings.append("That time had already passed today, so tomorrow was assumed")
    elif chosen.moment is not None:
        due = chosen.moment
        method = "EXPLICIT_DATE" if chosen.kind == "iso" else "RELATIVE"
    else:
        assert chosen.d is not None
        method = {"iso": "EXPLICIT_DATE", "explicit": "EXPLICIT_DATE", "numeric": "EXPLICIT_DATE", "ordinal": "EXPLICIT_DATE",
                  "weekday": "WEEKDAY"}.get(chosen.kind, "RELATIVE")
        clock_time: time | None = None
        if clock is not None:
            if clock.kind == "business":
                clock_time = ctx.business_day_end
                method = "BUSINESS_TERM" if method == "RELATIVE" else method
            elif clock.kind == "midnight":
                warnings.append("'midnight' was read as the end of that day")
            else:
                clock_time = clock.value
        elif chosen.time_hint is not None:
            clock_time = chosen.time_hint
        if clock_time is None:
            due = _end_of_day(chosen.d, tz)
            precision = DuePrecision.DATE
        else:
            naive = datetime.combine(chosen.d, clock_time)
            if isinstance(tz, ZoneInfo):
                status = wall_time_status(naive, tz)
                if status == "gap":
                    warnings.append(f"{clock_time:%H:%M} does not exist on {chosen.d} in {tz.key} (clocks spring forward); shifted forward")
                elif status == "ambiguous":
                    warnings.append(f"{clock_time:%H:%M} happens twice on {chosen.d} in {tz.key} (clocks fall back); used the first occurrence")
            due = _to_utc(naive, tz)

    if isinstance(tz, ZoneInfo):
        tz_name = tz.key
    else:
        offset = tz.utcoffset(None) or timedelta(0)
        tz_name = f"UTC{offset.total_seconds() / 3600:+g}"

    alt_instants: list[datetime] = []
    for d in alternatives:
        alt_instants.append(_end_of_day(d, tz) if precision == DuePrecision.DATE else _to_utc(datetime.combine(d, (clock.value if clock and clock.value else ctx.business_day_end)), tz))
    now = ctx.now or ctx.reference
    written_out = chosen is not None and chosen.kind in ("iso", "explicit", "numeric")
    explanation = f'Read "{raw}" as {fmt_when(due, precision, ctx.tz)}' + (
        "." if written_out else f" (relative to the message date, {today:%a} {today:%b} {today.day}, in {ctx.tz.key})."
    )
    return Resolution(
        due_at=due, precision=precision, method=method, matched_text=raw, timezone=tz_name,
        ambiguous=ambiguous, ambiguity=ambiguity, alternatives=alt_instants, warnings=warnings,
        explanation=explanation, in_past=due < now,
    )


def _units_candidate(m: re.Match[str], ctx: ResolverContext, ref_local: datetime, today: date) -> _Cand:
    raw_n = m["n"]
    n = int(raw_n) if raw_n.isdigit() else _NUMWORDS[raw_n]
    unit = m["unit"].rstrip("s")
    if n > _MAX_UNITS.get(unit, 3_650):
        raise _Implausible(f"'{m.group(0)}' is too far away to be a real deadline")
    if m["biz"]:
        if unit != "day":
            return _Cand("units", today, ambiguity="'business' only makes sense with days")
        return _Cand("units", _add_business_days(today, n))
    if unit in ("minute", "min"):
        return _Cand("units", moment=(ctx.reference + timedelta(minutes=n)).astimezone(UTC))
    if unit in ("hour", "hr"):
        return _Cand("units", moment=(ctx.reference + timedelta(hours=n)).astimezone(UTC))
    if unit == "day":
        return _Cand("units", today + timedelta(days=n))
    if unit == "week":
        return _Cand("units", today + timedelta(weeks=n))
    return _Cand("units", add_months(today, n))


def _period_candidate(kind: str, ctx: ResolverContext, today: date) -> _Cand:
    canon = kind.replace("end of the ", "end of ").replace("end of this ", "end of ")
    if canon in ("this week", "eow", "end of week"):
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        return _Cand("period", friday, time_hint=ctx.business_day_end)
    monday = today - timedelta(days=today.weekday())
    if canon == "end of next week":
        return _Cand("period", monday + timedelta(days=7 + 4), time_hint=ctx.business_day_end)
    if canon == "next week":
        start = monday + timedelta(days=7)
        return _Cand(
            "period", start, alts=[start, start + timedelta(days=4)],
            ambiguity="'next week' is not a specific day; the first day of next week was used (the message may mean any day, or the end of it)",
        )
    if canon in ("this month", "eom", "end of month"):
        return _Cand("period", today.replace(day=calendar.monthrange(today.year, today.month)[1]), time_hint=ctx.business_day_end)
    first_next = add_months(today.replace(day=1), 1)
    last_next = first_next.replace(day=calendar.monthrange(first_next.year, first_next.month)[1])
    if canon == "end of next month":
        return _Cand("period", last_next, time_hint=ctx.business_day_end)
    return _Cand(  # "next month"
        "period", first_next, alts=[first_next, last_next],
        ambiguity="'next month' is not a specific day; the first day of next month was used",
    )
