"""Human-approval flow: nothing external happens without an explicit decision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.enums import (
    ApprovalAction as AA,
)
from app.enums import (
    ApprovalStatus as A,
)
from app.enums import (
    AuditEventType,
    NotificationKind,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import ConflictError, ValidationFailed
from app.models import ApprovalRequest, AuditEvent, AutomationRun, CalendarEvent, Notification
from app.schemas.misc import ApprovalPatch
from app.services import approvals
from tests.factories import make_obligation, make_user

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
FOLLOW_UP = {"to": "hr@example.org", "subject": "Following up", "body": "Hi, just following up."}
EVENT = {
    "title": "Interview", "start_at": "2026-09-25T14:00:00+00:00", "end_at": "2026-09-25T15:00:00+00:00", "timezone": "UTC",
}


def _propose(db, action=AA.SEND_FOLLOW_UP, payload=None, **kw):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    a, created = approvals.propose(
        db, user=user, ob=ob, action=action, title="Send follow-up", payload=FOLLOW_UP if payload is None else payload,
        proposed_by="SYSTEM", rationale="r", now=NOW, settings=settings, **kw,
    )
    db.commit()
    return user, ob, a


def test_proposal_is_pending_audited_and_notifies_the_user_but_does_nothing_else(db):
    user, ob, a = _propose(db)
    assert a.status == A.PENDING and a.expires_at == NOW + timedelta(hours=settings.approval_ttl_hours)
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.APPROVAL_REQUESTED)) is not None
    kinds = {n.kind for n in db.scalars(select(Notification))}
    assert kinds == {NotificationKind.APPROVAL_REQUESTED}


def test_proposing_the_same_action_twice_returns_the_existing_request(db):
    user, ob, a = _propose(db)
    again, created = approvals.propose(
        db, user=user, ob=ob, action=AA.SEND_FOLLOW_UP, title="dup", payload=FOLLOW_UP, proposed_by="SYSTEM",
        rationale=None, now=NOW, settings=settings,
    )
    assert created is False and again.id == a.id


@pytest.mark.parametrize(
    "action,payload",
    [
        (AA.SEND_FOLLOW_UP, {"to": "not-an-email", "subject": "s", "body": "b"}),
        (AA.SEND_FOLLOW_UP, {"to": "a@example.net", "subject": "", "body": "b"}),
        (AA.SEND_FOLLOW_UP, {**FOLLOW_UP, "cc": "x@example.net"}),  # unknown field
        (AA.CREATE_CALENDAR_EVENT, {"title": "t"}),  # missing times
    ],
)
def test_invalid_payloads_are_rejected_before_anything_is_stored(db, action, payload):
    user = make_user(db)
    ob = make_obligation(db, user)
    with pytest.raises(ValidationFailed):
        approvals.propose(db, user=user, ob=ob, action=action, title="t", payload=payload, proposed_by="AI", rationale=None, now=NOW, settings=settings)
    assert db.scalar(select(ApprovalRequest.id)) is None


def test_only_a_pending_draft_can_be_edited_and_edits_are_revalidated(db):
    user, ob, a = _propose(db)
    approvals.edit(db, user, a, ApprovalPatch(subject="New subject", body="Edited body"), NOW)
    assert a.payload["subject"] == "New subject" and a.payload["to"] == "hr@example.org"
    with pytest.raises(ValidationFailed):
        approvals.edit(db, user, a, ApprovalPatch(start_at=NOW), NOW)  # not an editable field of a follow-up
    approvals.approve(db, user, a, NOW, settings)
    with pytest.raises(ConflictError):
        approvals.edit(db, user, a, ApprovalPatch(subject="too late"), NOW)


def test_approve_moves_to_approved_and_asks_for_n8n_only_for_external_actions(db):
    user, ob, a = _propose(db)
    _, needs_n8n = approvals.approve(db, user, a, NOW, settings)
    assert a.status == A.APPROVED and a.decided_at == NOW and needs_n8n is True


def test_approving_twice_is_idempotent_and_does_not_re_dispatch_after_execution(db):
    user, ob, a = _propose(db)
    approvals.approve(db, user, a, NOW, settings)
    _, again = approvals.approve(db, user, a, NOW, settings)
    assert again is True  # still waiting for n8n: dispatching again is harmless (claim is atomic)
    a.status = A.EXECUTED
    _, after = approvals.approve(db, user, a, NOW, settings)
    assert after is False


@pytest.mark.parametrize("terminal", [A.REJECTED, A.EXPIRED, A.CANCELLED])
def test_a_decided_or_dead_request_cannot_be_approved(db, terminal):
    user, ob, a = _propose(db)
    a.status = terminal
    with pytest.raises(ConflictError):
        approvals.approve(db, user, a, NOW, settings)


def test_reject_is_final_and_idempotent(db):
    user, ob, a = _propose(db)
    approvals.reject(db, user, a, NOW)
    approvals.reject(db, user, a, NOW)
    assert a.status == A.REJECTED
    with pytest.raises(ConflictError):
        approvals.approve(db, user, a, NOW, settings)


def test_claim_is_exclusive_and_only_returns_user_approved_external_actions(db):
    user, ob, pending = _propose(db)
    assert approvals.claim(db, NOW, settings) == []  # PENDING is never executable
    approvals.approve(db, user, pending, NOW, settings)
    db.commit()
    first = approvals.claim(db, NOW, settings)
    assert [x.id for x in first] == [pending.id] and pending.status == A.EXECUTING and pending.attempts == 1
    assert approvals.claim(db, NOW, settings) == []


def test_calendar_success_creates_the_event_links_it_and_schedules_the_obligation(db):
    user, ob, a = _propose(db, AA.CREATE_CALENDAR_EVENT, EVENT)
    approvals.approve(db, user, a, NOW, settings)
    approvals.claim(db, NOW, settings)
    approvals.report_result(
        db, a, ok=True, result={"provider": "LOCAL", "external_id": "evt-1", "url": "http://cal/evt-1"}, error=None,
        now=NOW, settings=settings,
    )
    db.commit()
    db.refresh(ob)
    ev = db.scalar(select(CalendarEvent))
    assert a.status == A.EXECUTED and ev.obligation_id == ob.id and ev.external_id == "evt-1" and ev.url == "http://cal/evt-1"
    assert ob.status == S.SCHEDULED
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.CALENDAR_EVENT_CREATED)) is not None


def test_a_failed_execution_is_retried_a_bounded_number_of_times_then_reported_failed(db):
    user, ob, a = _propose(db)
    approvals.approve(db, user, a, NOW, settings)
    for attempt in range(1, settings.n8n_max_attempts + 1):
        assert len(approvals.claim(db, NOW, settings)) == 1
        approvals.report_result(db, a, ok=False, result=None, error="SMTP refused", now=NOW, settings=settings)
        if attempt < settings.n8n_max_attempts:
            assert a.status == A.APPROVED  # goes back in the queue
    assert a.status == A.FAILED and a.error == "SMTP refused"
    assert approvals.claim(db, NOW, settings) == []
    failures = db.scalars(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.ACTION_FAILED)).all()
    assert failures[-1].data["will_retry"] is False
    assert any(n.kind == NotificationKind.ACTION_RESULT for n in db.scalars(select(Notification)))  # user is told


def test_a_duplicate_result_report_does_not_apply_twice(db):
    user, ob, a = _propose(db, AA.CREATE_CALENDAR_EVENT, EVENT)
    approvals.approve(db, user, a, NOW, settings)
    approvals.claim(db, NOW, settings)
    kw = dict(ok=True, result={"provider": "LOCAL", "external_id": "evt-1"}, error=None, now=NOW, settings=settings)
    approvals.report_result(db, a, **kw)
    approvals.report_result(db, a, **kw)
    db.commit()
    assert len(db.scalars(select(CalendarEvent)).all()) == 1
    assert len(db.scalars(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.CALENDAR_EVENT_CREATED)).all()) == 1


def test_a_stuck_execution_is_reclaimed_after_the_lease(db):
    user, ob, a = _propose(db)
    approvals.approve(db, user, a, NOW, settings)
    approvals.claim(db, NOW, settings)  # n8n takes it and dies
    assert approvals.claim(db, NOW + timedelta(seconds=30), settings) == []
    later = NOW + timedelta(seconds=settings.sending_lease_seconds + 1)
    assert len(approvals.claim(db, later, settings)) == 1 and a.attempts == 2


def test_approved_dismissal_is_applied_by_the_backend_itself_not_n8n(db):
    user, ob, a = _propose(db, AA.DISMISS_OBLIGATION, {})
    _, needs_n8n = approvals.approve(db, user, a, NOW, settings)
    db.refresh(ob)
    assert needs_n8n is False and a.status == A.EXECUTED and ob.status == S.DISMISSED


# ----------------------------------------------------------------------------- over HTTP
def _make_ob(db, email, **kw):
    from app.models import User

    user = db.scalar(select(User).where(User.email == email))
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org", counterparty_name="Dana Recruiter", **kw)
    db.commit()
    return ob


def test_follow_up_creates_a_draft_and_sends_nothing(alice, fake_n8n, db):
    ob = _make_ob(db, "alice@example.com")
    r = alice.post(f"/api/obligations/{ob.id}/follow-up")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "PENDING" and body["payload"]["to"] == "hr@example.org"
    assert "Hi Dana," in body["payload"]["body"] and "Alice Example" in body["payload"]["body"]
    assert fake_n8n.calls == []  # no external call until approval


def test_follow_up_without_a_recipient_is_a_clear_validation_error(alice, db):
    from app.models import User

    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1))
    db.commit()
    r = alice.post(f"/api/obligations/{ob.id}/follow-up")
    assert r.status_code == 422 and "no address to send a follow-up to" in r.json()["detail"]


def test_approving_over_http_triggers_n8n_exactly_once_with_only_the_id(alice, fake_n8n, db):
    ob = _make_ob(db, "alice@example.com")
    a = alice.post(f"/api/obligations/{ob.id}/follow-up").json()
    r = alice.post(f"/api/approvals/{a['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    assert fake_n8n.calls == [("commitmentos-action", {"approval_id": a["id"]})]  # no email content leaves via the trigger


def test_when_n8n_is_down_the_approval_survives_and_the_failure_is_visible(alice, fake_n8n, db):
    fake_n8n.fail_with = "ConnectError: n8n unreachable"
    ob = _make_ob(db, "alice@example.com")
    a = alice.post(f"/api/obligations/{ob.id}/follow-up").json()
    r = alice.post(f"/api/approvals/{a['id']}/approve")
    assert r.status_code == 200  # the user's decision is never lost because a downstream system is down
    assert alice.get(f"/api/approvals/{a['id']}").json()["status"] == "APPROVED"  # still queued for the scheduled pull
    runs = db.scalars(select(AutomationRun).where(AutomationRun.workflow_key == "api-dispatch")).all()
    assert len(runs) == 1 and runs[0].status.value == "FAILED" and "n8n" in runs[0].error


def test_schedule_is_a_user_approved_calendar_action_executed_by_n8n(alice, fake_n8n, db):
    ob = _make_ob(db, "alice@example.com")
    r = alice.post(f"/api/obligations/{ob.id}/schedule", json={"duration_minutes": 45})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "APPROVED" and body["action_type"] == "CREATE_CALENDAR_EVENT"
    start, end = datetime.fromisoformat(body["payload"]["start_at"]), datetime.fromisoformat(body["payload"]["end_at"])
    assert end - start == timedelta(minutes=45)
    assert len(fake_n8n.calls) == 1


def test_scheduling_twice_does_not_create_a_second_request(alice, fake_n8n, db):
    ob = _make_ob(db, "alice@example.com")
    a = alice.post(f"/api/obligations/{ob.id}/schedule").json()
    b = alice.post(f"/api/obligations/{ob.id}/schedule").json()
    assert a["id"] == b["id"] and len(fake_n8n.calls) == 1


def test_another_user_cannot_read_edit_approve_or_reject_someone_elses_approval(alice, bob, fake_n8n, db):
    ob = _make_ob(db, "alice@example.com")
    a = alice.post(f"/api/obligations/{ob.id}/follow-up").json()
    for method, path, body in [
        ("get", f"/api/approvals/{a['id']}", None),
        ("patch", f"/api/approvals/{a['id']}", {"subject": "pwned"}),
        ("post", f"/api/approvals/{a['id']}/approve", None),
        ("post", f"/api/approvals/{a['id']}/reject", None),
    ]:
        r = getattr(bob, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, (method, path, r.text)
    assert bob.get("/api/approvals").json() == []
    assert alice.get(f"/api/approvals/{a['id']}").json()["status"] == "PENDING"
    assert fake_n8n.calls == []
