"""Can user B touch user A's data? Every endpoint, every verb. The answer must always be 404 (not 403,
so ids cannot even be probed) and A's data must be byte-for-byte unchanged afterwards."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.enums import ApprovalAction, NotificationKind
from app.enums import ObligationStatus as S
from app.models import ApprovalRequest, AuditEvent, Notification, Obligation, User
from tests.factories import make_obligation

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def _victim_data(db):
    a = db.scalar(select(User).where(User.email == "alice@example.com"))
    ob = make_obligation(db, a, title="Alice private matter", status=S.OPEN, due_at=NOW + timedelta(days=2),
                         counterparty_email="hr@example.org", acknowledged_at=NOW)
    n = Notification(user_id=a.id, obligation_id=ob.id, kind=NotificationKind.REMINDER, channel="IN_APP", status="SENT",
                     title="secret reminder", body="b", dedupe_key="dk")
    ap = ApprovalRequest(user_id=a.id, obligation_id=ob.id, action_type=ApprovalAction.SEND_FOLLOW_UP, title="secret draft",
                         payload={"to": "hr@example.org", "subject": "s", "body": "b"})
    db.add_all([n, ap])
    db.flush()
    db.add(AuditEvent(user_id=a.id, obligation_id=ob.id, event_type="COMPLETED", actor_type="USER", message="secret audit"))
    db.commit()
    return ob, n, ap


def _snapshot(db, ob):
    db.expire_all()
    o = db.get(Obligation, ob.id)
    return (o.status, o.title, o.priority, o.due_at, o.snoozed_until, o.completed_at, o.updated_at,
            db.scalar(select(AuditEvent.id).order_by(AuditEvent.id.desc())))


OB_CALLS = [
    ("get", "", None), ("get", "/timeline", None),
    ("patch", "", {"title": "hacked"}), ("post", "/complete", {}), ("post", "/dismiss", {}),
    ("post", "/snooze", {"hours": 2}), ("post", "/approve", None), ("post", "/reopen", None),
    ("post", "/schedule", {}), ("post", "/follow-up", None),
]


@pytest.mark.parametrize("method,suffix,body", OB_CALLS, ids=[f"{m} {s or '/'}" for m, s, _ in OB_CALLS])
def test_another_user_gets_404_on_every_obligation_endpoint_and_nothing_changes(alice, bob, fake_n8n, db, method, suffix, body):
    ob, *_ = _victim_data(db)
    before = _snapshot(db, ob)
    kwargs = {"json": body} if body is not None else {}
    r = getattr(bob, method)(f"/api/obligations/{ob.id}{suffix}", **kwargs)
    assert r.status_code == 404, f"{method.upper()} {suffix}: {r.status_code} {r.text}"
    assert "Alice" not in r.text  # the error must not leak anything about the object
    assert _snapshot(db, ob) == before
    assert fake_n8n.calls == []


def test_the_owner_can_do_all_of_it(alice, fake_n8n, db):
    ob, *_ = _victim_data(db)
    for method, suffix, body in [("get", "", None), ("get", "/timeline", None), ("post", "/snooze", {"hours": 2}), ("post", "/follow-up", None)]:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(alice, method)(f"/api/obligations/{ob.id}{suffix}", **kwargs).status_code in (200, 201), (method, suffix)


def test_listings_and_feeds_never_include_another_users_rows(alice, bob, db):
    ob, n, ap = _victim_data(db)
    bob.post("/api/obligations", json={"title": "Bob's own thing"})
    assert [o["title"] for o in bob.get("/api/obligations").json()["items"]] == ["Bob's own thing"]
    assert bob.get("/api/obligations?q=Alice").json()["total"] == 0
    assert bob.get("/api/approvals").json() == []
    assert bob.get("/api/notifications").json() == {"items": [], "unread": 0}
    audit = bob.get("/api/audit").json()["items"]
    assert all("secret" not in e["message"] for e in audit)
    assert bob.get(f"/api/audit?obligation_id={ob.id}").json()["items"] == []
    dash = bob.get("/api/dashboard").json()
    assert "Alice" not in str(dash)
    assert alice.get("/api/obligations").json()["total"] == 1  # and A still sees hers


def test_notification_read_state_is_owner_only(alice, bob, db):
    _, n, _ = _victim_data(db)
    assert bob.post(f"/api/notifications/{n.id}/read").status_code == 404
    db.expire_all()
    assert db.get(Notification, n.id).read_at is None
    bob.post("/api/notifications/read-all")
    db.expire_all()
    assert db.get(Notification, n.id).read_at is None  # bob's "mark all read" cannot touch alice's
    assert alice.post(f"/api/notifications/{n.id}/read").status_code == 204


def test_an_email_action_token_only_works_on_the_obligation_it_was_issued_for(alice, bob, db):
    from app.security import create_action_token

    ob, *_ = _victim_data(db)
    other = make_obligation(db, db.scalar(select(User).where(User.email == "alice@example.com")), title="Second")
    db.commit()
    token = create_action_token(ob.user_id, ob.id, "complete", NOW)
    r = bob.post("/api/actions/redeem", json={"token": token})  # possession of the link IS the credential, by design...
    assert r.status_code == 200 and r.json()["obligation"]["id"] == str(ob.id)
    db.expire_all()
    assert db.get(Obligation, ob.id).status == S.COMPLETED and db.get(Obligation, other.id).status == S.OPEN  # ...but strictly scoped
    forged = create_action_token(ob.user_id, uuid.uuid4(), "complete", NOW)
    assert bob.post("/api/actions/redeem", json={"token": forged}).status_code == 404  # right user, wrong obligation
    assert bob.post("/api/actions/redeem", json={"token": "x" * 40}).status_code == 401


def test_expired_email_action_link_is_refused(alice, db):
    from app.security import create_action_token

    ob, *_ = _victim_data(db)
    token = create_action_token(ob.user_id, ob.id, "complete", NOW - timedelta(days=4))
    r = alice.post("/api/actions/redeem", json={"token": token})
    assert r.status_code == 401
    db.expire_all()
    assert db.get(Obligation, ob.id).status == S.OPEN


def test_email_action_link_completes_stops_reminders_and_is_audited(alice, db):
    from app.security import create_action_token

    ob, *_ = _victim_data(db)
    token = create_action_token(ob.user_id, ob.id, "complete", NOW)
    preview = alice.post("/api/actions/preview", json={"token": token}).json()
    assert preview["obligation"]["title"] == "Alice private matter" and preview["obligation"]["status"] == "OPEN"
    db.expire_all()
    assert db.get(Obligation, ob.id).status == S.OPEN  # previewing (what a mail scanner does) changes nothing
    first = alice.post("/api/actions/redeem", json={"token": token}).json()
    again = alice.post("/api/actions/redeem", json={"token": token}).json()
    assert first["changed"] is True and again["changed"] is False
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "COMPLETED", AuditEvent.obligation_id == ob.id))
    assert ev is not None
    db.expire_all()
    assert db.get(Obligation, ob.id).completed_via == "EMAIL_LINK"


# ---- system-wide automation runs are shared by everyone, so they must not carry free text (regression found by the live tests)
def _run(db, *, user=None, status="FAILED", error=None, result=None, workflow_key="notification-dispatcher"):
    from app.enums import RunStatus
    from app.models import AutomationRun

    run = AutomationRun(user_id=user.id if user else None, workflow_key=workflow_key, workflow_name="Notification Dispatcher", status=RunStatus(status),
                        error=error, result=result, started_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC), created_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC))
    db.add(run)
    db.flush()
    return run


def _emails(payload) -> str:
    import json

    return json.dumps(payload)


def test_a_system_wide_run_shows_status_and_counts_but_never_text_that_could_name_another_user(alice, bob, db):
    bobs = db.scalar(select(User).where(User.email == "bob@example.com"))
    _run(db, error='SMTP 550 <bob@example.com>: recipient address rejected', result={"sent": 3, "failed": 1, "errors": ["550 bob@example.com"], "note": "for bob@example.com", "ok": True})
    _run(db, user=bobs, error="Bob's own failure mentioning bob@example.com", result={"detail": "bob@example.com"}, workflow_key="incoming-detection")
    db.commit()

    for view in (alice.get("/api/automation/runs").json(), alice.get("/api/dashboard").json()["automation"]):
        items = view["items"] if "items" in view else view["recent_runs"]
        assert "bob@example.com" not in _emails(items)  # neither Bob's own run nor the text of the shared one
        (shared,) = items
        assert shared["status"] == "FAILED" and shared["error"] == "This workflow run failed; the operator log has the details."
        assert shared["result"] == {"sent": 3, "failed": 1, "ok": True}  # numbers and flags only

    own = bob.get("/api/automation/runs").json()["items"]
    mine = next(r for r in own if r["workflow_key"] == "incoming-detection")
    assert mine["error"] == "Bob's own failure mentioning bob@example.com" and mine["result"] == {"detail": "bob@example.com"}  # the owner sees everything


def test_the_dashboard_counts_only_the_runs_the_viewer_may_see(alice, bob, db):
    bobs = db.scalar(select(User).where(User.email == "bob@example.com"))
    alices = db.scalar(select(User).where(User.email == "alice@example.com"))
    for _ in range(3):
        _run(db, user=bobs, status="WAITING", workflow_key="follow-up-assistant")
        _run(db, user=bobs, status="FAILED", workflow_key="incoming-detection", error="x")
    _run(db, user=alices, status="WAITING", workflow_key="follow-up-assistant")
    _run(db, status="FAILED", error="system")
    db.commit()
    clock.freeze(datetime(2026, 9, 24, 13, 0, tzinfo=UTC))
    a = alice.get("/api/dashboard").json()["automation"]
    assert a["waiting"] == 1 and a["failed_24h"] == 1  # hers plus the shared one; Bob's three of each are invisible to her
    b = bob.get("/api/dashboard").json()["automation"]
    assert b["waiting"] == 3 and b["failed_24h"] == 4
