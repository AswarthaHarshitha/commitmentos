"""API -> n8n: trigger a workflow webhook.

Failure model: this never raises. It retries transient failures a bounded number of times
(connection errors, timeouts, 5xx, 429 - never 4xx) and reports the outcome; callers decide what a
failure means (usually: leave the work queued in the database so the scheduled n8n pull picks it up,
and record a FAILED automation run so the failure is visible).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger("commitmentos.n8n")

SECRET_HEADER = "X-CommitmentOS-Key"  # noqa: S105 - a header *name*, not a secret


@dataclass(frozen=True)
class TriggerResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None
    attempts: int = 0


class N8nClient:
    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        timeout: float = 8.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_seconds
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> N8nClient:
        s = settings or get_settings()
        return cls(
            s.n8n_base_url,
            s.n8n_outbound_secret.get_secret_value(),
            timeout=s.n8n_timeout_seconds,
            max_attempts=s.n8n_max_attempts,
        )

    def trigger(self, path: str, payload: dict[str, Any]) -> TriggerResult:
        url = f"{self._base}/webhook/{path.lstrip('/')}"
        last_error = "unknown error"
        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                    response = client.post(url, json=payload, headers={SECRET_HEADER: self._secret})
                last_status = response.status_code
                if response.is_success:
                    return TriggerResult(True, response.status_code, None, attempt)
                last_error = f"n8n responded {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break  # a 4xx will not fix itself; do not hammer
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: n8n unreachable"
            if attempt < self._max_attempts:
                time.sleep(self._backoff * (2 ** (attempt - 1)))
        log.warning("n8n trigger %s failed after %d attempt(s): %s", path, attempt, last_error)
        return TriggerResult(False, last_status, last_error, attempt)


def get_n8n_client() -> N8nClient:
    """FastAPI dependency (overridden in tests)."""
    return N8nClient.from_settings()
