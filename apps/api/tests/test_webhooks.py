"""The n8n -> API contract: idempotent run reporting, delivery reports, strict validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.enums import ApprovalStatus, RunStatus
from app.enums import NotificationChannel as Ch
from app.enums import NotificationKind as K
from app.enums import NotificationStatus as NS
from app.models import ApprovalRequest, AutomationRun, Notification, User
from app.services import approvals, messages, notifications
from app.services.reminders import policy_for
from tests.factories import make_obligation, make_user

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
URL = "/api/webhooks/n8n"


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def post(client, headers, body):
    return client.post(URL, json=body, headers=headers)


def started(**over):
    return {"event": "run.started", "workflow_key": "incoming-detection", "n8n_execution_id": "1001",
            "n8n_workflow_id": "wf-1", "trigger": "GMAIL_POLL", **over}


def test_run_started_creates_a_running_row_with_the_n8n_execution_id(client, n8n_headers, db):
    r = post(client, n8n_headers, started())
    assert r.status_code == 200 and r.json()["ok"] is True
    run = db.scalar(select(AutomationRun))
    assert (run.workflow_key, run.workflow_name, run.n8n_execution_id, run.trigger) == ("incoming-detection", "Commitment Detection", "1001", "GMAIL_POLL")
    assert run.status == RunStatus.RUNNING and run.started_at == NOW


def test_a_retried_run_started_does_not_create_a_second_row(client, n8n_headers, db):
    ids = {post(client, n8n_headers, started()).json()["run_id"] for _ in range(3)}
    assert len(ids) == 1 and len(db.scalars(select(AutomationRun)).all()) == 1


def test_run_finished_records_status_duration_and_result(client, n8n_headers, db):
    post(client, n8n_headers, started())
    clock.freeze(NOW + timedelta(seconds=2))
    r = post(client, n8n_headers, {"event": "run.finished", "n8n_execution_id": "1001", "status": "SUCCESS",
                                   "result": {"decision": "CREATE", "obligation_id": "abc"}, "duration_ms": 1400})
    assert r.json()["status"] == "SUCCESS"
    db.expire_all()
    run = db.scalar(select(AutomationRun))
    assert run.status == RunStatus.SUCCESS and run.duration_ms == 1400 and run.result["decision"] == "CREATE" and run.finished_at is not None


def test_duration_falls_back_to_the_clock_when_n8n_does_not_send_one(client, n8n_headers, db):
    post(client, n8n_headers, started())
    clock.freeze(NOW + timedelta(seconds=3))
    post(client, n8n_headers, {"event": "run.finished", "n8n_execution_id": "1001"})
    db.expire_all()
    assert db.scalar(select(AutomationRun)).duration_ms == 3000


def test_run_failed_marks_the_failure_and_the_node_that_broke(client, n8n_headers, db):
    post(client, n8n_headers, started())
    post(client, n8n_headers, {"event": "run.failed", "n8n_execution_id": "1001", "error": "LLM timeout after 45s", "error_node": "AI Extraction"})
    db.expire_all()
    run = db.scalar(select(AutomationRun))
    assert run.status == RunStatus.FAILED and run.error == "LLM timeout after 45s" and run.error_node == "AI Extraction"


def test_a_finish_for_a_run_we_never_saw_start_is_still_recorded(client, n8n_headers, db):
    post(client, n8n_headers, {"event": "run.failed", "n8n_execution_id": "77", "workflow_key": "deadline-monitor", "error": "boom"})
    run = db.scalar(select(AutomationRun))
    assert run.n8n_execution_id == "77" and run.workflow_key == "deadline-monitor" and run.status == RunStatus.FAILED


def test_the_first_final_status_wins_over_a_late_duplicate(client, n8n_headers, db):
    post(client, n8n_headers, started())
    post(client, n8n_headers, {"event": "run.failed", "n8n_execution_id": "1001", "error": "real failure"})
    post(client, n8n_headers, {"event": "run.finished", "n8n_execution_id": "1001", "status": "SUCCESS"})
    db.expire_all()
    assert db.scalar(select(AutomationRun)).status == RunStatus.FAILED


def test_a_workflow_waiting_for_approval_is_reported_as_waiting_not_finished(client, n8n_headers, db):
    post(client, n8n_headers, started(workflow_key="follow-up-assistant", status="WAITING"))
    run = db.scalar(select(AutomationRun))
    assert run.status == RunStatus.WAITING and run.finished_at is None
    post(client, n8n_headers, {"event": "run.finished", "n8n_execution_id": "1001", "status": "SUCCESS"})
    db.expire_all()
    assert db.scalar(select(AutomationRun)).status == RunStatus.SUCCESS


def test_runs_are_attributed_to_the_user_by_email(client, n8n_headers, db):
    user = make_user(db, "runner@example.com")
    db.commit()
    post(client, n8n_headers, started(user_email="Runner@Example.com"))
    assert db.scalar(select(AutomationRun)).user_id == user.id


@pytest.mark.parametrize(
    "body",
    [
        {"event": "nonsense"},
        {"event": "run.started"},  # missing required fields
        started(extra_field="x"),  # unknown field
        started(status="EXPLODED"),
        started(n8n_execution_id="x" * 65),
        {"event": "run.finished", "n8n_execution_id": "1", "duration_ms": -5},
        {"event": "run.finished", "n8n_execution_id": "1", "status": "NOT_A_STATUS"},
        {"event": "notification.sent", "notification_id": "not-a-uuid"},
        {"event": "proposal.created", "obligation_id": "6b0d0d6c-1111-4c1c-9c7b-2f0a1d3b5e77", "action_type": "WIPE_DISK", "title": "t"},
    ],
)
def test_malformed_events_are_rejected_with_422_and_change_nothing(client, n8n_headers, db, body):
    assert post(client, n8n_headers, body).status_code == 422
    assert db.scalars(select(AutomationRun)).all() == []


def _pending_email(db):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(hours=5))
    policy = policy_for(user, settings)
    created = notifications.queue(db, user=user, ob=ob, kind=K.REMINDER, rung_key="T-6h",
                                  content=messages.build(K.REMINDER, ob, policy.tz, NOW, settings), policy=policy, now=NOW)
    note = next(n for n in created if n.channel == Ch.EMAIL)
    db.commit()
    return note


def test_notification_sent_report_flips_status_and_links_the_run(client, n8n_headers, db):
    note = _pending_email(db)
    post(client, n8n_headers, started(workflow_key="notification-dispatcher"))
    r = post(client, n8n_headers, {"event": "notification.sent", "notification_id": str(note.id), "n8n_execution_id": "1001"})
    assert r.json()["status"] == "SENT"
    db.expire_all()
    fresh = db.get(Notification, note.id)
    assert fresh.status == NS.SENT and fresh.sent_at == NOW and fresh.automation_run_id is not None


def test_notification_failed_report_schedules_a_retry(client, n8n_headers, db):
    note = _pending_email(db)
    notifications.claim_pending(db, NOW, settings)
    db.commit()
    r = post(client, n8n_headers, {"event": "notification.failed", "notification_id": str(note.id), "error": "SMTP 421"})
    assert r.json()["status"] == "PENDING"
    db.expire_all()
    assert db.get(Notification, note.id).next_attempt_at > NOW


def test_reports_about_unknown_entities_are_404_not_500(client, n8n_headers):
    ghost = "6b0d0d6c-1111-4c1c-9c7b-2f0a1d3b5e77"
    assert post(client, n8n_headers, {"event": "notification.sent", "notification_id": ghost}).status_code == 404
    assert post(client, n8n_headers, {"event": "approval.executed", "approval_id": ghost}).status_code == 404
    assert post(client, n8n_headers, {"event": "proposal.created", "obligation_id": ghost, "action_type": "COMPLETE_OBLIGATION", "title": "t"}).status_code == 404


def test_proposal_event_creates_a_pending_approval_only(client, n8n_headers, db):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1))
    db.commit()
    body = {"event": "proposal.created", "obligation_id": str(ob.id), "action_type": "COMPLETE_OBLIGATION",
            "title": "Mark complete?", "rationale": "Your reply says it is done", "proposed_by": "AI"}
    first = post(client, n8n_headers, body).json()
    second = post(client, n8n_headers, body).json()
    assert first["created"] is True and second["created"] is False and first["approval_id"] == second["approval_id"]
    a = db.scalar(select(ApprovalRequest))
    assert a.status == ApprovalStatus.PENDING and a.proposed_by == "AI"
    db.expire_all()
    assert db.get(type(ob), ob.id).status.value == "OPEN"  # a proposal changes nothing by itself


def test_approval_execution_report_over_the_wire(client, n8n_headers, db):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    a, _ = approvals.propose(db, user=user, ob=ob, action="SEND_FOLLOW_UP", title="Send follow-up", proposed_by="SYSTEM",
                             payload={"to": "hr@example.org", "subject": "s", "body": "b"}, rationale=None, now=NOW, settings=settings)
    approvals.approve(db, user, a, NOW, settings)
    approvals.claim(db, NOW, settings)
    db.commit()
    r = post(client, n8n_headers, {"event": "approval.executed", "approval_id": str(a.id), "result": {"message_id": "<m1@mail>"}})
    assert r.json()["status"] == "EXECUTED"
    dup = post(client, n8n_headers, {"event": "approval.executed", "approval_id": str(a.id), "result": {"message_id": "<m1@mail>"}})
    assert dup.status_code == 200 and dup.json()["status"] == "EXECUTED"


# ------------------------------------------------------------------ internal endpoints n8n polls
def test_monitor_tick_endpoint_runs_the_deterministic_rules(client, n8n_headers, db):
    user = make_user(db)
    ob = make_obligation(db, user, status="ACTION_REQUIRED", due_at=NOW - timedelta(minutes=5))
    ob.next_action_at = NOW - timedelta(minutes=1)
    db.commit()
    body = client.post("/api/internal/monitor/tick", headers=n8n_headers).json()
    assert body["evaluated"] == 1 and body["marked_overdue"] == 1 and body["reminders_queued"] == 1 and body["errors"] == []
    again = client.post("/api/internal/monitor/tick", headers=n8n_headers).json()
    assert again["evaluated"] == 0  # idempotent


def test_claim_endpoint_hands_out_delivery_ready_emails_once(client, n8n_headers, db):
    note = _pending_email(db)
    first = client.post("/api/internal/notifications/claim", headers=n8n_headers, json={"limit": 10}).json()
    assert first["count"] == 1 and first["items"][0]["id"] == str(note.id)
    item = first["items"][0]
    assert item["to_email"].endswith("@example.com") and item["subject"] and "<html" not in item["text"] and item["html"].startswith("<div")
    assert client.post("/api/internal/notifications/claim", headers=n8n_headers).json()["count"] == 0


def test_claim_endpoint_validates_its_input(client, n8n_headers):
    assert client.post("/api/internal/notifications/claim", headers=n8n_headers, json={"limit": 0}).status_code == 422
    assert client.post("/api/internal/notifications/claim", headers=n8n_headers, json={"limit": 5, "x": 1}).status_code == 422


def test_approval_push_path_claims_atomically_so_a_duplicate_trigger_is_harmless(client, n8n_headers, db):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    a, _ = approvals.propose(db, user=user, ob=ob, action="SEND_FOLLOW_UP", title="Send follow-up", proposed_by="SYSTEM",
                             payload={"to": "hr@example.org", "subject": "s", "body": "b"}, rationale=None, now=NOW, settings=settings)
    approvals.approve(db, user, a, NOW, settings)
    db.commit()
    first = client.get(f"/api/internal/approvals/{a.id}", headers=n8n_headers).json()
    second = client.get(f"/api/internal/approvals/{a.id}", headers=n8n_headers).json()
    assert first["claimed"] is True and first["item"]["payload"]["to"] == "hr@example.org" and first["item"]["user_email"] == user.email
    assert second["claimed"] is False and "EXECUTING" in second["reason"]
    assert client.get("/api/internal/approvals/not-a-uuid", headers=n8n_headers).json()["claimed"] is False


def test_pending_approvals_are_never_handed_to_n8n_by_either_path(client, n8n_headers, db):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="hr@example.org")
    a, _ = approvals.propose(db, user=user, ob=ob, action="SEND_FOLLOW_UP", title="t", proposed_by="AI",
                             payload={"to": "hr@example.org", "subject": "s", "body": "b"}, rationale=None, now=NOW, settings=settings)
    db.commit()
    assert client.post("/api/internal/approvals/claim", headers=n8n_headers).json()["count"] == 0
    assert client.get(f"/api/internal/approvals/{a.id}", headers=n8n_headers).json()["claimed"] is False
    assert db.get(ApprovalRequest, a.id).status == ApprovalStatus.PENDING
    assert db.scalar(select(User.id)) is not None
