"""message.extracted -> database state: thresholds, idempotency, dedup, trust boundary, privacy, failures."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.clock import clock
from app.models import AuditEvent, Notification, Obligation, Source
from tests.helpers_extraction import BODY, QUOTE, make_extraction, make_message

NOW = datetime(2026, 9, 23, 15, 5, tzinfo=UTC)  # Wed 11:05 in New York; the message below arrived 5 minutes earlier
URL = "/api/webhooks/n8n"


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def event(message=None, extraction=None, **over):
    ev = {"event": "message.extracted", "user_email": "alice@example.com", "message": make_message(**(message or {})),
          "extraction": make_extraction(**(extraction or {})), "n8n_execution_id": "exec-1"}
    ev.update(over)
    return ev


def commit(client, headers, **kw):
    r = client.post(URL, json=event(**kw), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def obligation(alice, oid):
    return alice.get(f"/api/obligations/{oid}").json()


# ------------------------------------------------------------------ the threshold policy end to end
def test_high_confidence_message_creates_an_open_obligation_with_a_deadline_computed_by_code(alice, n8n_headers, db):
    result = commit(alice, n8n_headers)
    assert result["disposition"] == "OBLIGATION_CREATED" and result["status"] == "OPEN" and result["decision"]["action"] == "CREATE"
    d = obligation(alice, result["obligation_id"])
    ob = d["obligation"]
    assert ob["status"] == "OPEN" and ob["source"] == "GMAIL" and ob["confidence"] == 0.95 and ob["priority"] == "HIGH"
    assert ob["due_at"] == "2026-09-24T21:00:00Z" and ob["due_precision"] == "DATETIME" and ob["due_text"] == "by tomorrow 5pm"
    assert ob["due_resolution"]["method"] == "RELATIVE" and "relative to the message date" in ob["due_resolution"]["explanation"]
    assert ob["counterparty_email"] == "hr@example.org" and ob["counterparty_name"] == "Dana Whitfield" and ob["owner"] == "me"  # the sender, as their own mail names them
    assert ob["acknowledged_at"] is None  # lands in the Commitment Inbox
    assert ob["next_action_at"] == "2026-09-23T21:00:00Z"  # T-24h reminder
    u = d["understanding"]
    assert u["explanation"].startswith("The sender asks you") and u["source_context"] == QUOTE and u["deadline_text"] == "by tomorrow 5pm"
    src = d["sources"][0]
    assert src["subject"] == "Internship paperwork" and QUOTE in src["excerpt"] and src["disposition"] == "OBLIGATION_CREATED" and src["role"] == "PRIMARY"


def test_the_audit_trail_tells_the_whole_story_in_order(alice, n8n_headers):
    oid = commit(alice, n8n_headers)["obligation_id"]
    events = alice.get(f"/api/audit?obligation_id={oid}").json()["items"]
    story = [e["event_type"] for e in reversed(events)]
    assert story == ["COMMITMENT_DETECTED", "OBLIGATION_CREATED", "NOTIFICATION_QUEUED"]
    everything = [e["event_type"] for e in reversed(alice.get("/api/audit").json()["items"])]
    assert everything[:4] == ["SECURITY", "MESSAGE_RECEIVED", "AI_CLASSIFIED", "COMMITMENT_DETECTED"]  # account, then the pipeline
    created = next(e for e in events if e["event_type"] == "OBLIGATION_CREATED")
    assert created["data"]["thresholds"] == {"high": 0.85, "medium": 0.6} and created["actor_type"] == "SYSTEM"
    classified = next(e for e in alice.get("/api/audit").json()["items"] if e["event_type"] == "AI_CLASSIFIED")
    assert classified["actor_type"] == "AI" and "94" not in classified["message"] and "95%" in classified["message"]


def test_a_high_priority_deadline_notifies_in_app_and_by_email(alice, n8n_headers, db):
    commit(alice, n8n_headers)
    assert sorted(n.channel.value for n in db.scalars(select(Notification))) == ["EMAIL", "IN_APP"]


@pytest.mark.parametrize(
    "extraction,body_change,external",
    [
        ({"priority": "LOW", "obligation_type": "TASK", "deadline_text": "by December 20"}, ("by tomorrow 5pm", "by December 20"), False),  # far away, low priority
        ({"priority": "HIGH", "obligation_type": "TASK", "deadline_text": "by December 20"}, ("by tomorrow 5pm", "by December 20"), True),  # high priority
        ({"priority": "LOW", "obligation_type": "TASK", "deadline_text": "by December 20", "requires_confirmation": True}, ("by tomorrow 5pm", "by December 20"), True),
        ({"priority": "LOW", "obligation_type": "TASK"}, None, True),  # due within 72 hours
    ],
    ids=["quiet", "high-priority", "needs-reply", "due-soon"],
)
def test_email_is_sent_only_when_it_matters(alice, n8n_headers, db, extraction, body_change, external):
    msg = BODY if body_change is None else BODY.replace(*body_change)
    quote = QUOTE if body_change is None else QUOTE.replace(*body_change)
    commit(alice, n8n_headers, message={"body": msg}, extraction={**extraction, "source_context": quote})
    channels = {n.channel.value for n in db.scalars(select(Notification))}
    assert ("EMAIL" in channels) is external and "IN_APP" in channels


def test_medium_confidence_needs_review_and_explains_why(alice, n8n_headers, db):
    result = commit(alice, n8n_headers, extraction={"confidence": 0.7})
    ob = obligation(alice, result["obligation_id"])["obligation"]
    assert result["disposition"] == "NEEDS_REVIEW" and ob["status"] == "NEEDS_REVIEW" and ob["next_action_at"] is None  # not monitored until accepted
    assert "below the auto-create threshold" in ob["ambiguity"]
    assert {n.kind.value for n in db.scalars(select(Notification))} == {"NEEDS_REVIEW"}
    assert alice.get("/api/obligations?view=inbox").json()["total"] == 1
    accepted = alice.post(f"/api/obligations/{ob['id']}/approve").json()["obligation"]
    assert accepted["status"] == "OPEN" and accepted["next_action_at"] is not None  # accepting starts the reminder ladder


def test_low_confidence_becomes_a_candidate_not_an_obligation_and_can_be_promoted_or_discarded(alice, n8n_headers, db):
    result = commit(alice, n8n_headers, extraction={"confidence": 0.4})
    assert result["disposition"] == "CANDIDATE" and "obligation_id" not in result
    assert alice.get("/api/obligations").json()["total"] == 0
    cands = alice.get("/api/candidates").json()
    assert len(cands) == 1 and cands[0]["title"] == "Submit internship documents" and cands[0]["confidence"] == 0.4
    assert cands[0]["sender_email"] == "hr@example.org" and QUOTE in cands[0]["excerpt"] and cands[0]["explanation"] and cands[0]["due_at"] == "2026-09-24T21:00:00Z"
    assert db.scalar(select(Notification.id)) is None  # a weak guess does not interrupt anyone

    promoted = alice.post(f"/api/candidates/{cands[0]['id']}/promote")
    assert promoted.status_code == 201 and promoted.json()["status"] == "NEEDS_REVIEW" and promoted.json()["due_at"] == "2026-09-24T21:00:00Z"
    assert alice.post(f"/api/candidates/{cands[0]['id']}/promote").status_code == 409  # only once
    assert alice.get("/api/candidates").json() == []

    commit(alice, n8n_headers, message={"external_id": "m2", "thread_id": "t2"}, extraction={"confidence": 0.3, "title": "Renew the lease"})
    second = alice.get("/api/candidates").json()[0]
    assert alice.post(f"/api/candidates/{second['id']}/discard").status_code == 204
    assert alice.post(f"/api/candidates/{second['id']}/discard").status_code == 204  # idempotent
    assert alice.get("/api/candidates?status=DISCARDED").json()[0]["id"] == second["id"]
    assert alice.post(f"/api/candidates/{second['id']}/promote").status_code == 409


def test_candidates_are_private_to_their_owner(alice, bob, n8n_headers):
    commit(alice, n8n_headers, extraction={"confidence": 0.4})
    cid = alice.get("/api/candidates").json()[0]["id"]
    assert bob.get("/api/candidates").json() == []
    assert bob.post(f"/api/candidates/{cid}/promote").status_code == 404 and bob.post(f"/api/candidates/{cid}/discard").status_code == 404


def test_a_message_that_is_not_an_obligation_leaves_no_content_behind(alice, n8n_headers, db):
    result = commit(alice, n8n_headers, extraction={"is_obligation": False, "title": None, "source_context": None, "deadline_text": None, "confidence": 0.97})
    assert result["disposition"] == "NOT_OBLIGATION"
    src = db.scalar(select(Source))
    assert (src.subject, src.excerpt, src.sender_email, src.sender_name, src.extraction) == (None, None, None, None, None)
    assert src.content_hash and src.external_id == "gmail-001"  # ids/hashes only: enough for idempotency, nothing readable
    assert db.scalar(select(func.count()).select_from(Obligation)) == 0 and db.scalar(select(Notification.id)) is None
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "NOT_AN_OBLIGATION")) is not None


def test_confirmation_requests_start_as_action_required(alice, n8n_headers):
    oid = commit(alice, n8n_headers, extraction={"requires_confirmation": True})["obligation_id"]
    assert obligation(alice, oid)["obligation"]["status"] == "ACTION_REQUIRED"


def test_a_pasted_email_keeps_its_own_source_type(alice, n8n_headers):
    oid = commit(alice, n8n_headers, message={"source_type": "IMPORTED"})["obligation_id"]
    d = obligation(alice, oid)
    assert d["obligation"]["source"] == "IMPORTED" and "is_synthetic" not in d["sources"][0]


# ------------------------------------------------------------------ deadlines
def test_relative_deadlines_resolve_against_the_message_date_not_the_processing_date(alice, n8n_headers):
    result = commit(alice, n8n_headers, message={"received_at": "2026-09-18T15:00:00Z"})  # mail is five days old
    ob = obligation(alice, result["obligation_id"])["obligation"]
    assert ob["due_at"] == "2026-09-19T21:00:00Z" and ob["status"] == "NEEDS_REVIEW"
    assert "already passed" in ob["ambiguity"] and ob["due_resolution"]["in_past"] is True


def test_an_ambiguous_deadline_is_flagged_with_its_alternatives(alice, n8n_headers):
    body = BODY.replace("by tomorrow 5pm", "by next Friday")
    result = commit(alice, n8n_headers, message={"body": body},
                    extraction={"deadline_text": "by next Friday", "source_context": QUOTE.replace("by tomorrow 5pm", "by next Friday")})
    d = obligation(alice, result["obligation_id"])
    assert d["obligation"]["status"] == "NEEDS_REVIEW" and "ambiguous" in d["obligation"]["ambiguity"]
    assert len(d["understanding"]["alternatives"]) == 2 and d["obligation"]["due_at"] == "2026-09-26T03:59:59Z"


# ------------------------------------------------------------------ idempotency
def test_a_redelivered_webhook_is_a_harmless_no_op(alice, n8n_headers, db):
    first = commit(alice, n8n_headers)
    second = commit(alice, n8n_headers)
    assert second["duplicate_event"] is True and second["obligation_id"] == first["obligation_id"]
    assert db.scalar(select(func.count()).select_from(Obligation)) == 1 and db.scalar(select(func.count()).select_from(Source)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == 2  # no second round of notifications


def test_simultaneous_deliveries_of_the_same_message_create_exactly_one_obligation(alice, n8n_headers, db):
    from fastapi.testclient import TestClient

    from app.main import app

    barrier = threading.Barrier(4)

    def deliver(_):
        with TestClient(app, base_url="http://localhost:3000") as c:
            barrier.wait(timeout=10)
            r = c.post(URL, json=event(), headers=n8n_headers)
            return r.status_code, r.json()

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(deliver, range(4)))
    assert [code for code, _ in results] == [200, 200, 200, 200]  # nobody gets a 500 from the unique-constraint race
    assert db.scalar(select(func.count()).select_from(Obligation)) == 1 and db.scalar(select(func.count()).select_from(Source)) == 1
    assert sum(1 for _, body in results if not body.get("duplicate_event")) == 1


def test_the_same_external_id_for_two_different_users_is_not_a_duplicate(alice, bob, n8n_headers, db):
    commit(alice, n8n_headers)
    result = commit(alice, n8n_headers, user_email="bob@example.com", n8n_execution_id="exec-2")
    assert result["disposition"] == "OBLIGATION_CREATED"
    assert db.scalar(select(func.count()).select_from(Obligation)) == 2
    assert bob.get("/api/obligations").json()["total"] == 1 and alice.get("/api/obligations").json()["total"] == 1


def test_preflight_check_lets_n8n_skip_the_llm_for_messages_already_processed(alice, n8n_headers):
    ask = {"user_email": "alice@example.com", "source_type": "GMAIL", "external_id": "gmail-001"}
    assert alice.post("/api/internal/messages/check", json=ask, headers=n8n_headers).json() == {"seen": False}
    oid = commit(alice, n8n_headers)["obligation_id"]
    seen = alice.post("/api/internal/messages/check", json=ask, headers=n8n_headers).json()
    assert seen == {"seen": True, "disposition": "OBLIGATION_CREATED", "obligation_id": oid}
    assert alice.post("/api/internal/messages/check", json={**ask, "user_email": "nobody@example.com"}, headers=n8n_headers).status_code == 404
    assert alice.post("/api/internal/messages/check", json=ask).status_code == 401  # secret required


# ------------------------------------------------------------------ deduplication
def test_original_reminder_forward_and_calendar_copy_collapse_into_one_obligation(alice, n8n_headers, db):
    first = commit(alice, n8n_headers)["obligation_id"]
    reminder = commit(alice, n8n_headers, message={"external_id": "gmail-002", "thread_id": "t-9", "sender_email": "noreply@example.net", "subject": "Reminder: paperwork"},
                      extraction={"title": "Reminder: Submit internship documents"})
    forward = commit(alice, n8n_headers, message={"external_id": "gmail-003", "thread_id": "t-fwd", "sender_email": "friend@example.com", "subject": "Fwd: Internship paperwork"},
                     extraction={"title": "Fwd: Please submit internship documents"})
    calendar = commit(alice, n8n_headers, message={"source_type": "GOOGLE_CALENDAR", "external_id": "cal-77", "thread_id": None, "sender_email": None},
                      extraction={"title": "Submit the internship documents"})
    for dup in (reminder, forward, calendar):
        assert dup["disposition"] == "DUPLICATE" and dup["obligation_id"] == first
    assert db.scalar(select(func.count()).select_from(Obligation)) == 1
    sources = obligation(alice, first)["sources"]
    assert len(sources) == 4 and [s["role"] for s in sources] == ["PRIMARY", "DUPLICATE", "DUPLICATE", "DUPLICATE"]
    assert {s["source_type"] for s in sources} == {"GMAIL", "GOOGLE_CALENDAR"}  # every original source is kept as a reference
    merged = [e for e in alice.get("/api/audit").json()["items"] if e["event_type"] == "DUPLICATE_MERGED"]
    assert len(merged) == 3 and all(e["data"]["score"] >= 0.72 for e in merged)


def test_the_same_title_with_a_clearly_different_deadline_elsewhere_is_a_separate_obligation(alice, n8n_headers, db):
    commit(alice, n8n_headers)
    body = BODY.replace("by tomorrow 5pm", "by November 30")
    other = commit(alice, n8n_headers, message={"external_id": "gmail-050", "thread_id": "t-other", "body": body},
                   extraction={"deadline_text": "by November 30", "source_context": QUOTE.replace("by tomorrow 5pm", "by November 30")})
    assert other["disposition"] == "OBLIGATION_CREATED" and db.scalar(select(func.count()).select_from(Obligation)) == 2


def test_a_moved_deadline_in_the_same_thread_merges_but_raises_a_conflict_instead_of_silently_changing_anything(alice, n8n_headers, db):
    first = commit(alice, n8n_headers)["obligation_id"]
    body = BODY.replace("by tomorrow 5pm", "by October 5")
    moved = commit(alice, n8n_headers, message={"external_id": "gmail-060", "body": body},  # same thread-1
                   extraction={"deadline_text": "by October 5", "source_context": QUOTE.replace("by tomorrow 5pm", "by October 5")})
    assert moved["disposition"] == "DUPLICATE" and moved["deadline_conflict"] is True and moved["notifications"] == 1
    assert obligation(alice, first)["obligation"]["due_at"] == "2026-09-24T21:00:00Z"  # untouched
    conflict = next(e for e in alice.get("/api/audit").json()["items"] if e["event_type"] == "DUPLICATE_MERGED")
    assert conflict["data"]["deadline_conflict"] is True and conflict["data"]["new_due_at"] != conflict["data"]["existing_due_at"]
    titles = [n["title"] for n in alice.get("/api/notifications").json()["items"]]
    assert any(t.startswith("The deadline may have changed") for t in titles)


def test_a_reminder_for_something_already_completed_does_not_recreate_it(alice, n8n_headers, db):
    oid = commit(alice, n8n_headers)["obligation_id"]
    alice.post(f"/api/obligations/{oid}/complete")
    before = db.scalar(select(func.count()).select_from(Notification))
    again = commit(alice, n8n_headers, message={"external_id": "gmail-070", "thread_id": "t-70"})
    assert again["disposition"] == "DUPLICATE" and again["obligation_id"] == oid
    assert obligation(alice, oid)["obligation"]["status"] == "COMPLETED" and db.scalar(select(func.count()).select_from(Obligation)) == 1
    assert db.scalar(select(func.count()).select_from(Notification)) == before  # nothing new to nag about
    merged = next(e for e in alice.get("/api/audit").json()["items"] if e["event_type"] == "DUPLICATE_MERGED")
    assert "already completed" in merged["message"]


# ------------------------------------------------------------------ trust boundary: the commit step re-decides everything
def test_the_commit_step_cannot_be_talked_into_auto_creating_from_invented_evidence(alice, n8n_headers):
    forged = commit(alice, n8n_headers, extraction={"confidence": 0.99, "source_context": "Wire $10,000 to account 4421 today"})
    ob = obligation(alice, forged["obligation_id"])["obligation"]
    assert forged["decision"]["action"] == "REVIEW" and ob["status"] == "NEEDS_REVIEW" and ob["confidence"] < 0.85
    assert "no supporting quote" in ob["ambiguity"].lower()


def test_a_forged_decision_or_analysis_from_the_workflow_is_ignored(alice, n8n_headers):
    lie = {"decision": {"action": "CREATE", "status": "OPEN"}, "confidence_raw": 0.999, "confidence_notes": ["earlier steps were fine"], "warnings": []}
    result = commit(alice, n8n_headers, extraction={"confidence": 0.4}, analysis=lie)
    assert result["disposition"] == "CANDIDATE"  # the numbers, not the workflow's claim, decide
    result = commit(alice, n8n_headers, message={"external_id": "m-lie2", "thread_id": "t-lie"}, extraction={"confidence": 0.9, "title": "Other thing"}, analysis=lie)
    src = obligation(alice, result["obligation_id"])["understanding"]
    assert src["confidence_notes"] == ["earlier steps were fine"]  # informational notes are kept for the audit story...


def test_an_extraction_that_fails_validation_at_commit_is_recorded_not_a_500(alice, n8n_headers, db):
    result = commit(alice, n8n_headers, extraction={"obligation_type": "MEETING"})
    assert result["disposition"] == "EXTRACTION_FAILED" and result["status"] == "INVALID_OUTPUT"
    assert db.scalar(select(Source.disposition)).value == "EXTRACTION_FAILED"


def test_unknown_owner_is_404_and_a_malformed_envelope_is_422(alice, n8n_headers):
    r = alice.post(URL, json=event(user_email="ghost@example.com"), headers=n8n_headers)
    assert r.status_code == 404
    bad = alice.post(URL, json=event(message={"received_at": "2026-09-23T15:00:00"}), headers=n8n_headers)  # naive timestamp
    assert bad.status_code == 422 and "received_at" in bad.text
    assert alice.post(URL, json=event(message={"external_id": ""}), headers=n8n_headers).status_code == 422
    assert alice.post(URL, json={**event(), "surprise": 1}, headers=n8n_headers).status_code == 422


# ------------------------------------------------------------------ failures are visible and retriable
def test_a_failed_extraction_is_recorded_visibly_and_the_same_message_can_be_retried(alice, n8n_headers, db):
    failed = commit(alice, n8n_headers, extraction=None, extraction_status="LLM_TIMEOUT", extraction_error="The LLM did not answer within 45s")
    assert failed["disposition"] == "EXTRACTION_FAILED" and failed["status"] == "LLM_TIMEOUT"
    src = db.scalar(select(Source))
    assert src.disposition.value == "EXTRACTION_FAILED" and src.subject == "Internship paperwork" and src.sender_email == "hr@example.org" and src.excerpt is None  # identifiable, but no body
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event_type == "EXTRACTION_FAILED")) is not None
    titles = [n["title"] for n in alice.get("/api/notifications").json()["items"]]
    assert "CommitmentOS couldn't analyse a message" in titles
    ask = {"user_email": "alice@example.com", "source_type": "GMAIL", "external_id": "gmail-001"}
    assert alice.post("/api/internal/messages/check", json=ask, headers=n8n_headers).json() == {"seen": False}  # a failure must not block a retry

    retried = commit(alice, n8n_headers, n8n_execution_id="exec-retry")
    assert retried["disposition"] == "OBLIGATION_CREATED" and not retried.get("duplicate_event")
    assert db.scalar(select(func.count()).select_from(Source)) == 1  # the failed row was upgraded in place, not duplicated
    db.expire_all()
    assert db.scalar(select(Source.disposition)).value == "OBLIGATION_CREATED"


def test_repeated_failures_for_one_message_notify_once(alice, n8n_headers, db):
    for i in range(3):
        commit(alice, n8n_headers, extraction=None, extraction_status="LLM_UNAVAILABLE", extraction_error="503", n8n_execution_id=f"exec-f{i}")
    assert db.scalar(select(func.count()).select_from(Notification)) == 1
    assert db.scalar(select(func.count()).select_from(Source)) == 1


# ------------------------------------------------------------------ privacy & observability
def test_immutable_audit_rows_never_contain_message_content(alice, n8n_headers, db):
    commit(alice, n8n_headers)
    commit(alice, n8n_headers, message={"external_id": "m-priv2"}, extraction={"confidence": 0.3, "title": "Renew the lease"})
    commit(alice, n8n_headers, message={"external_id": "m-priv3"}, extraction={"is_obligation": False, "title": None, "source_context": None, "deadline_text": None})
    commit(alice, n8n_headers, message={"external_id": "m-priv4"}, extraction=None, extraction_status="LLM_TIMEOUT", extraction_error="slow")
    blob = json.dumps([(e.message, e.data) for e in db.scalars(select(AuditEvent))], default=str)
    for private in ("Internship paperwork", "Dana", "signed internship documents", "finalise onboarding", "hr@example.org", QUOTE):
        assert private not in blob, private


def test_the_automation_run_is_linked_to_the_user_and_the_obligation_it_produced(alice, n8n_headers):
    alice.post(URL, json={"event": "run.started", "workflow_key": "incoming-detection", "n8n_execution_id": "exec-1", "trigger": "WEBHOOK"}, headers=n8n_headers)
    oid = commit(alice, n8n_headers)["obligation_id"]
    runs = obligation(alice, oid)["runs"]
    assert len(runs) == 1 and runs[0]["workflow_key"] == "incoming-detection" and runs[0]["n8n_execution_id"] == "exec-1" and runs[0]["obligation_id"] == oid


def test_the_webhook_requires_the_secret_for_this_event_too(alice):
    assert alice.post(URL, json=event()).status_code == 401
    assert alice.post(URL, json=event(), headers={"X-Webhook-Secret": "wrong"}).status_code == 401



# ------------------------------------------------------------------ fuzzy deduplication (fingerprints differ, so only scoring can merge)
def test_a_reworded_title_in_the_same_thread_from_the_same_sender_is_merged_by_similarity(alice, n8n_headers, db):
    first = commit(alice, n8n_headers, extraction={"title": "Submit signed internship documents"})["obligation_id"]
    reworded = commit(alice, n8n_headers, message={"external_id": "gmail-090"}, extraction={"title": "Submit internship documents"})  # same thread + sender
    assert reworded["disposition"] == "DUPLICATE" and reworded["obligation_id"] == first and reworded["score"] >= 0.72
    assert db.scalar(select(func.count()).select_from(Obligation)) == 1
    merged = next(e for e in alice.get("/api/audit").json()["items"] if e["event_type"] == "DUPLICATE_MERGED")
    assert any(r.startswith("title_similarity=") for r in merged["data"]["reasons"]) and "same_thread" in merged["data"]["reasons"]


def test_a_reworded_title_with_nothing_else_in_common_is_deliberately_not_merged(alice, n8n_headers, db):
    """False merges hide real obligations, so the matcher errs towards keeping two rather than losing one."""
    commit(alice, n8n_headers, extraction={"title": "Submit signed internship documents"})
    other = commit(alice, n8n_headers, message={"external_id": "gmail-091", "thread_id": "elsewhere", "sender_email": "someone@example.net"},
                   extraction={"title": "Submit internship documents"})
    assert other["disposition"] == "OBLIGATION_CREATED" and db.scalar(select(func.count()).select_from(Obligation)) == 2


def test_unrelated_commitments_in_the_same_thread_are_never_merged(alice, n8n_headers, db):
    commit(alice, n8n_headers)
    other = commit(alice, n8n_headers, message={"external_id": "gmail-092"},
                   extraction={"title": "Book a dentist appointment", "deadline_text": "by tomorrow 5pm", "obligation_type": "APPOINTMENT"})
    assert other["disposition"] == "OBLIGATION_CREATED" and db.scalar(select(func.count()).select_from(Obligation)) == 2
