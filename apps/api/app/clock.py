"""Single source of "now" for every business rule.

All deadline / reminder / escalation logic takes its time from here (or from an explicit
``now`` argument), never from ``datetime.now()`` directly. That makes the rules
deterministic and testable, and lets the end-to-end suite fast-forward time so a 24-hour reminder
ladder can be exercised in seconds without any special-cased code paths.

* In tests: ``clock.freeze(dt)`` pins time.
* In the end-to-end stack only (TEST_CLOCK=true): ``clock.advance(delta)`` moves the virtual clock forward
  (persisted by ``services.testclock``). Everywhere else the offset is always zero.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset = timedelta(0)
        self._frozen: datetime | None = None

    def now(self) -> datetime:
        with self._lock:
            if self._frozen is not None:
                return self._frozen
            return datetime.now(UTC) + self._offset

    @property
    def offset(self) -> timedelta:
        return self._offset

    def set_offset(self, offset: timedelta) -> None:
        with self._lock:
            self._offset = offset

    def advance(self, delta: timedelta) -> datetime:
        with self._lock:
            if self._frozen is not None:
                self._frozen = self._frozen + delta
            else:
                self._offset = self._offset + delta
        return self.now()

    def freeze(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("clock.freeze() requires a timezone-aware datetime")
        with self._lock:
            self._frozen = at.astimezone(UTC)

    def unfreeze(self) -> None:
        with self._lock:
            self._frozen = None

    def reset(self) -> None:
        with self._lock:
            self._frozen = None
            self._offset = timedelta(0)


clock = Clock()


def utcnow() -> datetime:
    return clock.now()
