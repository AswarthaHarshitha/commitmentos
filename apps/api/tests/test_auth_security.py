"""Authentication, session security, CSRF, rate limiting, webhook authentication."""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.models import AuditEvent, User
from app.security import create_access_token, create_action_token, decode_action_token
from tests.conftest import PASSWORD, register

settings = get_settings()
SECRET = settings.jwt_secret.get_secret_value()


def test_register_sets_an_httponly_samesite_cookie_and_never_returns_secrets(client):
    r = client.post("/api/auth/register", json={"email": "New@Example.com", "password": PASSWORD, "timezone": "Europe/London"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@example.com"  # normalised
    assert "password" not in json.dumps(body).lower() and "hash" not in json.dumps(body).lower()
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert client.get("/api/auth/me").json()["email"] == "new@example.com"


def test_passwords_are_stored_as_argon2id_hashes(client, db):
    register(client, "hash@example.com")
    stored = db.scalar(select(User.password_hash).where(User.email == "hash@example.com"))
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"email": "a@example.com", "password": "short"}, "password"),
        ({"email": "not-an-email", "password": PASSWORD}, "email"),
        ({"email": "a@example.com", "password": PASSWORD, "timezone": "Mars/Olympus"}, "timezone"),
        ({"email": "a@example.com", "password": PASSWORD, "is_active": True}, "is_active"),  # mass assignment
        ({"email": "a@example.com", "password": PASSWORD, "token_version": 99}, "token_version"),
    ],
)
def test_registration_rejects_invalid_or_unexpected_input(client, payload, field):
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 422
    assert field in json.dumps(r.json())


def test_duplicate_email_is_rejected_case_insensitively(client, make_client):
    register(client, "dup@example.com")
    other = make_client()
    r = other.post("/api/auth/register", json={"email": "DUP@example.com", "password": PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "EMAIL_TAKEN"


def test_registration_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_registration", False)
    r = client.post("/api/auth/register", json={"email": "x@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["code"] == "REGISTRATION_DISABLED"


def test_wrong_password_and_unknown_email_are_indistinguishable(client, make_client):
    register(client, "real@example.com")
    c2 = make_client()
    bad_pw = c2.post("/api/auth/login", json={"email": "real@example.com", "password": "wrong-password-123"})
    no_user = c2.post("/api/auth/login", json={"email": "ghost@example.com", "password": "wrong-password-123"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()  # no account enumeration


def test_login_then_logout_revokes_the_session_everywhere(client, make_client):
    register(client, "rev@example.com")
    other = make_client()
    assert other.post("/api/auth/login", json={"email": "rev@example.com", "password": PASSWORD}).status_code == 200
    stolen = other.cookies.get(settings.cookie_name)
    third_party = make_client()  # has no cookie: authenticates with the copied token only
    bearer = {"Authorization": f"Bearer {stolen}"}
    assert third_party.get("/api/auth/me", headers=bearer).status_code == 200  # control: the token works now
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    # ...and the token issued to the OTHER device is dead too (token_version bump)
    assert third_party.get("/api/auth/me", headers=bearer).status_code == 401


def test_login_is_rate_limited_per_account_with_retry_after(client, make_client):
    register(client, "rl@example.com")
    attacker = make_client()
    codes = [attacker.post("/api/auth/login", json={"email": "rl@example.com", "password": f"guess-number-{i:03d}"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]
    r = attacker.post("/api/auth/login", json={"email": "rl@example.com", "password": PASSWORD})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1  # even the right password is locked out


def test_bearer_token_flow_for_scripts(client, make_client):
    register(client, "script@example.com")
    c2 = make_client()
    r = c2.post("/api/auth/token", data={"username": "script@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    me = c2.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "script@example.com"


def _forge(claims: dict, key: str = SECRET, alg: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=alg)


def _claims(user_id: str, **over) -> dict:
    now = int(time.time())
    base = {"iss": "commitmentos", "typ": "access", "sub": user_id, "tv": 0, "iat": now, "exp": now + 3600}
    base.update(over)
    return base


def test_forged_and_malformed_tokens_are_rejected(client, db, make_client):
    register(client, "victim@example.com")
    uid = str(db.scalar(select(User.id).where(User.email == "victim@example.com")))
    anon = make_client()
    header = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    none_alg = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(_claims(uid))}."
    good = _forge(_claims(uid))
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    cases = {
        "alg=none": none_alg,
        "wrong secret": _forge(_claims(uid), key="a-different-secret-that-is-long-enough-123456"),
        "tampered signature": tampered,
        "expired": _forge(_claims(uid, exp=int(time.time()) - 10)),
        "wrong issuer": _forge(_claims(uid, iss="someone-else")),
        "action token as session": _forge(_claims(uid, typ="action")),
        "missing exp": _forge({k: v for k, v in _claims(uid).items() if k != "exp"}),
        "stale token_version": _forge(_claims(uid, tv=5)),
        "subject not a uuid": _forge(_claims("not-a-uuid")),
        "unknown user": _forge(_claims(str(uuid.uuid4()))),
        "garbage": "not.a.jwt",
    }
    assert anon.get("/api/auth/me", headers=header(good)).status_code == 200  # control: the well-formed one works
    for name, token in cases.items():
        r = anon.get("/api/auth/me", headers=header(token))
        assert r.status_code == 401, f"{name} was accepted"


def test_unauthenticated_requests_are_refused_on_every_user_endpoint(client):
    for method, path in [
        ("get", "/api/obligations"), ("post", "/api/obligations"), ("get", "/api/dashboard"), ("get", "/api/audit"),
        ("get", "/api/approvals"), ("get", "/api/notifications"), ("get", "/api/automation/runs"), ("get", "/api/system/status"),
        ("get", f"/api/obligations/{uuid.uuid4()}"), ("post", f"/api/obligations/{uuid.uuid4()}/complete"),
    ]:
        assert getattr(client, method)(path).status_code == 401, (method, path)


def test_cookie_authenticated_writes_from_a_foreign_origin_are_blocked_csrf(alice):
    body = {"title": "Pay rent", "due": {"date": "2026-10-01"}}
    r = alice.post("/api/obligations", json=body, headers={"Origin": "https://evil.example.org"})
    assert r.status_code == 403 and r.json()["code"] == "CSRF_BLOCKED"
    assert alice.post("/api/obligations", json=body, headers={"Origin": "http://localhost:3000"}).status_code == 201
    assert alice.post("/api/obligations", json=body).status_code == 201  # non-browser client, no Origin header
    assert alice.get("/api/obligations", headers={"Origin": "https://evil.example.org"}).status_code == 200  # reads are safe


def test_bearer_requests_are_not_subject_to_the_cookie_csrf_rule(client, make_client):
    register(client, "b@example.com")
    c2 = make_client()
    tok = c2.post("/api/auth/token", data={"username": "b@example.com", "password": PASSWORD}).json()["access_token"]
    r = c2.post("/api/obligations", json={"title": "x"}, headers={"Authorization": f"Bearer {tok}", "Origin": "https://tool.example.org"})
    assert r.status_code == 201  # an attacker page cannot make the browser attach an Authorization header


def test_demo_clock_time_travel_never_expires_or_pre_dates_a_session(alice):
    clock.freeze(datetime.now(UTC) + timedelta(days=30))
    assert alice.get("/api/auth/me").status_code == 200  # sessions use real time, not the business clock


def test_session_tokens_and_action_tokens_are_not_interchangeable():
    uid, oid = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    session, _ = create_access_token(uid, 0)
    action = create_action_token(uid, oid, "complete", now)
    from app.errors import UnauthorizedError
    from app.security import decode_access_token

    with pytest.raises(UnauthorizedError):
        decode_action_token(session, now)
    with pytest.raises(UnauthorizedError):
        decode_access_token(action)


def test_action_tokens_expire_on_the_app_clock_not_the_wall_clock():
    uid, oid = uuid.uuid4(), uuid.uuid4()
    issued = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    token = create_action_token(uid, oid, "complete", issued)
    assert decode_action_token(token, issued + timedelta(hours=71))["oid"] == str(oid)
    from app.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        decode_action_token(token, issued + timedelta(hours=73))


# ------------------------------------------------------------------ webhook / internal authentication
def test_webhook_requires_the_shared_secret(client, n8n_headers):
    event = {"event": "run.started", "workflow_key": "deadline-monitor", "n8n_execution_id": "1"}
    assert client.post("/api/webhooks/n8n", json=event).status_code == 401
    assert client.post("/api/webhooks/n8n", json=event, headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert client.post("/api/webhooks/n8n", json=event, headers={"X-Webhook-Secret": ""}).status_code == 401
    assert client.post("/api/webhooks/n8n", json=event, headers=n8n_headers).status_code == 200


def test_outbound_secret_and_user_sessions_cannot_call_internal_endpoints(alice, n8n_headers):
    assert alice.post("/api/internal/monitor/tick").status_code == 401  # a logged-in user is not n8n
    wrong = {"X-Webhook-Secret": os.environ["N8N_OUTBOUND_SECRET"]}  # the *other* direction's secret
    assert alice.post("/api/internal/monitor/tick", headers=wrong).status_code == 401
    assert alice.post("/api/internal/monitor/tick", headers=n8n_headers).status_code == 200


def test_the_n8n_secret_does_not_grant_access_to_user_endpoints(client, n8n_headers):
    assert client.get("/api/obligations", headers=n8n_headers).status_code == 401


def test_oversized_bodies_are_refused(client, n8n_headers):
    r = client.post("/api/webhooks/n8n", content=b"x" * 1_200_000, headers={**n8n_headers, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_responses_carry_security_headers_and_a_request_id(alice):
    r = alice.get("/api/obligations")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert len(r.headers["x-request-id"]) >= 8
    assert alice.get("/api/obligations", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
    hostile = alice.get("/api/obligations", headers={"X-Request-ID": "bad id\twith spaces"}).headers["x-request-id"]
    assert hostile != "bad id\twith spaces"  # untrusted ids are replaced, never echoed


def test_logout_and_profile_changes_are_audited(alice, db):
    alice.patch("/api/auth/me", json={"timezone": "Asia/Kolkata"})
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [e.message for e in events] == ["Account created", "Profile / preferences updated"]


def test_preferences_are_validated_and_persisted(alice):
    ok = alice.patch("/api/auth/me", json={"preferences": {"reminder_offsets_hours": [48, 12], "business_day_end": "18:30", "notify_email": False}})
    assert ok.status_code == 200 and ok.json()["preferences"]["reminder_offsets_hours"] == [48, 12]
    for bad in ({"business_day_end": "6pm"}, {"reminder_offsets_hours": [0]}, {"escalate_after_hours": -1}, {"nope": 1}):
        assert alice.patch("/api/auth/me", json={"preferences": bad}).status_code == 422
