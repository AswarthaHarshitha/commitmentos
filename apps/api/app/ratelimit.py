"""Small in-memory sliding-window rate limiter.

Per-process by design (the app runs as a single process; see docs/architecture.md). If the API
is ever scaled horizontally this must move to a shared store - noted in the README limitations.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request

from app.errors import RateLimited


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(0.0, hits[0] + self.window - now)
            hits.append(now)
            if len(self._hits) > 50_000:  # crude memory bound
                self._evict(now)
            return True, 0.0

    def _evict(self, now: float) -> None:
        for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(limiter: SlidingWindowLimiter, scope: str, key_fn: Callable[[Request], str] = client_ip):
    """FastAPI dependency factory."""

    def dependency(request: Request) -> None:
        allowed, retry_after = limiter.check(f"{scope}:{key_fn(request)}")
        if not allowed:
            raise RateLimited(
                "Too many requests, slow down and try again shortly",
                extra={"retry_after": max(1, int(retry_after) + 1)},
            )

    return dependency
