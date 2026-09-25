"""A workflow that proposes something for a human ends WAITING, and is resolved when the human has decided."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.enums import ApprovalStatus, RunStatus
from app.models import ApprovalRequest, AutomationRun, User
from app.services import approvals, monitor
from tests.factories import make_obligation

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
WEBHOOK = "/api/webhooks/n8n"


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def post(client, headers, body):
    return client.post(WEBHOOK, json=body, headers=headers)


def start(client, headers, execution="2001", key="follow-up-assistant"):
    post(client, headers, {"event": "run.started", "workflow_key": key, "n8n_execution_id": execution})


def propose(client, headers, ob, *, execution="2001", action="COMPLETE_OBLIGATION", **extra):
    r = post(client, headers, {"event": "proposal.created", "obligation_id": str(ob.id), "action_type": action, "title": f"{action} {ob.title}",
                               "proposed_by": "AI", "n8n_execution_id": execution, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def finish_waiting(client, headers, execution="2001"):
    post(client, headers, {"event": "run.finished", "n8n_execution_id": execution, "status": "WAITING", "duration_ms": 850, "result": {"proposals": 2}})


def me(db) -> User:
    return db.scalar(select(User))


def run_of(db, execution: str = "2001") -> AutomationRun:
    db.expire_all()
    return db.scalar(select(AutomationRun).where(AutomationRun.n8n_execution_id == execution))


def test_the_proposal_is_linked_to_the_run_that_made_it(alice, n8n_headers, db):
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=1))
    db.commit()
    start(alice, n8n_headers)
    propose(alice, n8n_headers, ob)
    db.expire_all()
    run = db.scalar(select(AutomationRun))
    assert db.scalar(select(ApprovalRequest)).automation_run_id == run.id


def test_a_waiting_run_stays_waiting_until_every_proposal_is_decided_then_resolves(alice, n8n_headers, db):
    first = make_obligation(db, me(db), title="Send the form", due_at=NOW + timedelta(days=1))
    second = make_obligation(db, me(db), title="Book the venue", due_at=NOW + timedelta(days=2))
    db.commit()
    start(alice, n8n_headers)
    a = propose(alice, n8n_headers, first)["approval_id"]
    b = propose(alice, n8n_headers, second)["approval_id"]
    finish_waiting(alice, n8n_headers)
    run = run_of(db)
    assert run.status == RunStatus.WAITING and run.finished_at is None and run.duration_ms == 850  # n8n's own working time, not the human's

    assert alice.post(f"/api/approvals/{a}/approve").status_code == 200  # COMPLETE is applied by the backend itself
    assert run_of(db).status == RunStatus.WAITING  # one decision is still outstanding

    assert alice.post(f"/api/approvals/{b}/reject").status_code == 200
    run = run_of(db)
    assert run.status == RunStatus.SUCCESS and run.finished_at == NOW and run.duration_ms == 850
    assert run.result["proposals"] == 2 and run.result["resolved"] == {"executed": 1, "rejected": 1}


def test_a_human_who_decides_before_n8n_finishes_reporting_does_not_leave_the_run_waiting_forever(alice, n8n_headers, db):
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=1))
    db.commit()
    start(alice, n8n_headers)
    approval_id = propose(alice, n8n_headers, ob)["approval_id"]
    assert alice.post(f"/api/approvals/{approval_id}/reject").status_code == 200  # decided while the run is still RUNNING
    assert run_of(db).status == RunStatus.RUNNING
    finish_waiting(alice, n8n_headers)  # n8n's final report arrives late
    assert run_of(db).status == RunStatus.SUCCESS


def test_an_approval_that_expires_unanswered_also_resolves_the_run(alice, n8n_headers, db):
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=30))
    db.commit()
    start(alice, n8n_headers)
    propose(alice, n8n_headers, ob)
    finish_waiting(alice, n8n_headers)
    clock.freeze(NOW + timedelta(hours=settings.approval_ttl_hours, minutes=1))
    monitor.run_tick(db, clock.now(), settings)
    db.commit()
    run = run_of(db)
    assert run.status == RunStatus.SUCCESS and run.result["resolved"] == {"expired": 1}


def test_closing_the_obligation_withdraws_the_proposal_and_resolves_the_run(alice, n8n_headers, db):
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    db.commit()
    start(alice, n8n_headers)
    propose(alice, n8n_headers, ob, action="SEND_FOLLOW_UP", payload={"to": "hr@example.org", "subject": "s", "body": "b"})
    finish_waiting(alice, n8n_headers)
    assert alice.post(f"/api/obligations/{ob.id}/complete").status_code == 200
    run = run_of(db)
    assert run.status == RunStatus.SUCCESS and run.result["resolved"] == {"cancelled": 1}


def test_an_approved_action_that_fails_for_good_marks_the_run_failed(alice, n8n_headers, db, fake_n8n):
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    db.commit()
    start(alice, n8n_headers)
    approval_id = propose(alice, n8n_headers, ob, action="SEND_FOLLOW_UP", payload={"to": "hr@example.org", "subject": "s", "body": "b"})["approval_id"]
    finish_waiting(alice, n8n_headers)
    assert alice.post(f"/api/approvals/{approval_id}/approve").status_code == 200
    assert run_of(db).status == RunStatus.WAITING  # approved, not yet executed
    for _ in range(settings.n8n_max_attempts):  # n8n claims and fails, up to the bounded number of attempts
        approvals.claim(db, clock.now(), settings)
        db.commit()
        post(alice, n8n_headers, {"event": "approval.failed", "approval_id": approval_id, "error": "SMTP refused the recipient"})
    db.expire_all()
    assert db.scalar(select(ApprovalRequest)).status == ApprovalStatus.FAILED
    run = run_of(db)
    assert run.status == RunStatus.FAILED and run.result["resolved"] == {"failed": 1} and "failed" in (run.error or "")


def test_a_run_waiting_on_something_else_is_left_alone(alice, n8n_headers, db):
    start(alice, n8n_headers)
    finish_waiting(alice, n8n_headers)  # no approvals linked
    ob = make_obligation(db, me(db), due_at=NOW + timedelta(days=1))
    db.commit()
    start(alice, n8n_headers, execution="9999")
    other = propose(alice, n8n_headers, ob, execution="9999")["approval_id"]  # an unrelated run's proposal, decided now
    alice.post(f"/api/approvals/{other}/reject")
    assert run_of(db, "9999").status == RunStatus.RUNNING  # only runs that are WAITING are resolved
    assert run_of(db).status == RunStatus.WAITING


def test_a_proposal_for_a_closed_obligation_is_a_harmless_noop_not_an_error_n8n_would_retry(alice, n8n_headers, db):
    ob = make_obligation(db, me(db), status="COMPLETED", due_at=NOW + timedelta(days=1))
    db.commit()
    body = propose(alice, n8n_headers, ob)
    assert body["created"] is False and body["approval_id"] is None and "completed" in body["reason"]
    assert db.scalar(select(ApprovalRequest)) is None
