"""The test clock: one virtual clock that every rule and every service agrees on.

All deadline logic reads time from ``app.clock``. In the end-to-end stack that clock can be moved forward so a
24-hour reminder ladder plays out in seconds, with no special-cased code path. The offset is persisted, so an API
restart does not snap back to real time. Only reachable when TEST_CLOCK=true.

Limits (by design, documented): the clock is per process, so it assumes a single API worker; it is a shared,
instance-wide clock, so it must never be enabled next to real users' data - the end-to-end suite runs in its own
stack for exactly that reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.clock import clock
from app.models import SystemSetting

_KEY = "test_clock"
MAX_ADVANCE = timedelta(days=90)


def restore_clock(db: Session) -> None:
    """Re-apply the persisted offset (called once at start-up when the test clock is enabled)."""
    row = db.get(SystemSetting, _KEY)
    seconds = float((row.value or {}).get("offset_seconds", 0)) if row is not None else 0.0
    clock.set_offset(timedelta(seconds=seconds))


def _save(db: Session) -> None:
    value = {"offset_seconds": clock.offset.total_seconds()}
    row = db.get(SystemSetting, _KEY)
    if row is None:
        db.add(SystemSetting(key=_KEY, value=value))
    else:
        row.value = value
    db.flush()


def state() -> dict[str, Any]:
    return {"now": clock.now(), "real_now": datetime.now(UTC), "offset_seconds": int(clock.offset.total_seconds())}


def advance(db: Session, delta: timedelta) -> dict[str, Any]:
    if not timedelta(0) < delta <= MAX_ADVANCE:
        raise ValueError(f"advance must be between 1 second and {MAX_ADVANCE.days} days")
    clock.advance(delta)
    _save(db)
    return state()


def set_to(db: Session, target: datetime) -> dict[str, Any]:
    if target.tzinfo is None:
        raise ValueError("the target time must include a UTC offset")
    clock.set_offset(target.astimezone(UTC) - datetime.now(UTC))
    _save(db)
    return state()


def reset(db: Session) -> dict[str, Any]:
    clock.set_offset(timedelta(0))
    _save(db)
    return state()
