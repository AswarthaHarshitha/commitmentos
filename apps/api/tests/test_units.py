"""Small units: log redaction, n8n client retry policy, rate limiter."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.logging_config import JsonFormatter, RedactingFilter, redact
from app.ratelimit import SlidingWindowLimiter
from app.services.n8n_client import SECRET_HEADER, N8nClient


# ---------------------------------------------------------------- safe logging
@pytest.mark.parametrize(
    "raw,secret",
    [
        ("Authorization: Bearer abcdefghijklmnop1234567890", "abcdefghijklmnop1234567890"),
        ("calling with token=s3cr3tvalue123 now", "s3cr3tvalue123"),
        ('{"password": "hunter2hunter2", "email": "a@b.c"}', "hunter2hunter2"),
        ("api_key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
        ("X-Webhook-Secret: 0123456789abcdef0123456789abcdef", "0123456789abcdef0123456789abcdef"),
        ("session eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
        ("Cookie: cos_session=abcdef123456", "abcdef123456"),
    ],
)
def test_secrets_are_redacted_from_log_text(raw, secret):
    cleaned = redact(raw)
    assert secret not in cleaned and "REDACTED" in cleaned


def test_json_log_lines_are_redacted_and_structured():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "login with password=%s", ("topsecretpw1",), None)
    record.request_id = "abc"
    assert RedactingFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))
    assert "topsecretpw1" not in json.dumps(line) and line["request_id"] == "abc" and line["level"] == "INFO"


def test_ordinary_log_text_is_left_alone():
    assert redact("Reminder queued for obligation 6b0d0d6c (2 channels)") == "Reminder queued for obligation 6b0d0d6c (2 channels)"


# ---------------------------------------------------------------- n8n client
def _client(handler, attempts=3):
    return N8nClient("http://n8n:5678", "outbound-secret", max_attempts=attempts, backoff_seconds=0, transport=httpx.MockTransport(handler))


def test_trigger_posts_json_to_the_webhook_path_with_the_secret_header():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(url=str(request.url), key=request.headers.get(SECRET_HEADER), body=json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    result = _client(handler).trigger("commitmentos-action", {"approval_id": "x"})
    assert result.ok and result.attempts == 1
    assert seen == {"url": "http://n8n:5678/webhook/commitmentos-action", "key": "outbound-secret", "body": {"approval_id": "x"}}


def test_transient_5xx_is_retried_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200)

    result = _client(handler).trigger("p", {})
    assert result.ok and result.attempts == 3 and len(calls) == 3


def test_connection_errors_are_retried_a_bounded_number_of_times_then_reported_not_raised():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused")

    result = _client(handler, attempts=3).trigger("p", {})
    assert not result.ok and result.attempts == 3 and len(calls) == 3  # never infinite
    assert "unreachable" in result.error


def test_timeouts_are_retried_too():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    assert _client(handler, attempts=2).trigger("p", {}).attempts == 2


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_client_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)

    result = _client(handler).trigger("p", {})
    assert not result.ok and len(calls) == 1 and result.status_code == status


def test_429_is_retried_like_a_transient_error():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200)

    assert _client(handler).trigger("p", {}).ok


# ---------------------------------------------------------------- rate limiter
def test_sliding_window_blocks_then_recovers():
    t = [0.0]
    lim = SlidingWindowLimiter(3, 10, clock=lambda: t[0])
    assert [lim.check("k")[0] for _ in range(4)] == [True, True, True, False]
    allowed, retry = lim.check("k")
    assert not allowed and 9 <= retry <= 10
    t[0] = 10.1
    assert lim.check("k")[0] is True  # window slid past the first hits


def test_limiter_keys_are_independent():
    lim = SlidingWindowLimiter(1, 60)
    assert lim.check("a")[0] and lim.check("b")[0] and not lim.check("a")[0]
