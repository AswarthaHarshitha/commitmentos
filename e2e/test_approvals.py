"""Human approval: n8n proposes, a PERSON decides, n8n executes - and only then. Plus calendar events and completion.

Everything external (a follow-up email, a calendar invite) is real: n8n's SMTP node delivers to Mailpit, so the tests read
the actual messages. Nothing here ever executes without the user's approval.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
from conftest import (
    API,
    BODY,
    MAILPIT,
    N8N,
    T0,
    compose,
    extraction,
    ingest_payload,
    restart_api,
    wait_for_n8n,
    wait_until,
)
from test_monitor import DUE, at, stays, tick, wait_for_mail

FOLLOWUP_SCAN = "commitmentos-followup-scan"
CALENDAR_SCAN = "commitmentos-calendar-scan"
ACTION = "commitmentos-action"


def counterparty() -> str:
    return f"dana-{uuid.uuid4().hex[:8]}@example.org"


def overdue_commitment(n8n, stub, user, clock, mail, who: str, *, named: str | None = None, body: str = BODY):
    """A commitment that is overdue at DUE+2h, from an email sent by `who`. `named` is the address the language model reads out of
    the text (by default the sender's own)."""
    at(clock, T0)
    stub.reset(extraction(counterparty_email=named or who))
    out = n8n.call("commitmentos-ingest", ingest_payload(user, f"e2e-{uuid.uuid4().hex[:8]}", body=body, received_at=T0, sender_email=who), timeout=120).json()
    assert out["outcome"] == "processed", out
    wait_until(lambda: len(mail.messages(user.email)) == 1, what="the detection email")
    at(clock, DUE + timedelta(hours=2))
    tick(n8n)
    wait_for_mail(mail, user, 2, "Overdue:")
    return user.obligations()[0]


def pending(user, action_type):
    rows = user.get("/api/approvals", params={"status": "PENDING"}).json()
    return [a for a in rows if a["action_type"] == action_type]


def find_proposal(user, action_type, *, what):
    return wait_until(lambda: (p := pending(user, action_type)) and p[0], what=what)


def approval_status(user, approval_id):
    return user.get(f"/api/approvals/{approval_id}").json()["status"]


def run_status(app_db, run_id):
    return app_db.one("select status from automation_runs where id = %s", run_id)


# ------------------------------------------------------------------------------------------------ follow-up assistant
def test_a_follow_up_goes_to_the_original_sender_even_when_the_email_names_someone_else(n8n, stub, user, clock, mail):
    """The text of an email can mention other addresses (or try to steer a reply): the follow-up still goes to where it came from."""
    sender, mentioned = counterparty(), counterparty()
    body = f"{BODY}\n\nPlease also copy {mentioned} on your reply."  # the model's address is grounded in the text - and still not used
    ob = overdue_commitment(n8n, stub, user, clock, mail, sender, named=mentioned, body=body)
    assert ob["counterparty_email"] == sender

    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")
    assert proposal["payload"]["to"] == sender
    assert user.patch(f"/api/approvals/{proposal['id']}", json={"to": mentioned}).status_code == 422  # and it cannot be redirected by hand

    assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200
    wait_until(lambda: len(mail.messages(sender)) == 1, what="the follow-up at the original sender")
    assert stays(lambda: mail.messages(mentioned) == [], 5)  # nothing reached the address that was only mentioned


def test_a_follow_up_is_drafted_for_review_and_only_sent_after_approval(n8n, stub, user, clock, mail, app_db):
    who = counterparty()
    ob = overdue_commitment(n8n, stub, user, clock, mail, who)

    scan = n8n.call(FOLLOWUP_SCAN, {}, timeout=120).json()
    assert scan["created"] >= 1
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")
    assert proposal["obligation_id"] == ob["id"] and proposal["payload"]["to"] == who and proposal["proposed_by"] == "SYSTEM"
    assert "where this stands" in proposal["payload"]["body"] and proposal["payload"]["subject"] == f"Following up: {ob['title']}"

    # proposing is not doing: nothing went to the counterparty, the user was asked, the workflow is WAITING
    assert stays(lambda: mail.messages(who) == [])
    wait_for_mail(mail, user, 3, "Approval needed:")
    run_id = proposal["automation_run_id"]
    assert run_id and run_status(app_db, run_id) == "WAITING"

    # the user approves -> the API pushes to n8n -> the real email goes out
    assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200
    sent = wait_until(lambda: mail.messages(who), what="the follow-up email at the counterparty")
    assert len(sent) == 1 and sent[0]["Subject"] == f"Following up: {ob['title']}"
    detail = mail.detail(sent[0]["ID"])
    assert detail["ReplyTo"][0]["Address"] == user.email and ob["title"] in detail["Text"]  # replies come back to the user
    wait_until(lambda: approval_status(user, proposal["id"]) == "EXECUTED", what="the approval to be marked executed")
    wait_until(lambda: run_status(app_db, run_id) == "SUCCESS", what="the waiting run to resolve")

    # a follow-up is not proposed again straight away
    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    assert not [p for p in pending(user, "SEND_FOLLOW_UP") if p["obligation_id"] == ob["id"]]


def test_a_rejected_follow_up_is_never_sent(n8n, stub, user, clock, mail, app_db):
    who = counterparty()
    overdue_commitment(n8n, stub, user, clock, mail, who)
    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")
    assert user.post(f"/api/approvals/{proposal['id']}/reject").status_code == 200
    n8n.call(ACTION, {"approval_id": proposal["id"]}, timeout=60)  # even if something tries to run it, the API refuses to hand it over
    assert stays(lambda: mail.messages(who) == [], 5)
    assert approval_status(user, proposal["id"]) == "REJECTED"
    wait_until(lambda: run_status(app_db, proposal["automation_run_id"]) == "SUCCESS", what="the run to resolve after the rejection")


def test_a_pending_proposal_cannot_be_executed_by_calling_n8n_directly(n8n, stub, user, clock, mail):
    who = counterparty()
    overdue_commitment(n8n, stub, user, clock, mail, who)
    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")
    r = n8n.call(ACTION, {"approval_id": proposal["id"]}, timeout=60)  # the push webhook, without the user having approved
    assert r.status_code == 200 and r.json().get("idle") is True
    assert stays(lambda: mail.messages(who) == [], 4)
    assert approval_status(user, proposal["id"]) == "PENDING"


def test_a_duplicate_trigger_cannot_run_an_approved_action_twice(n8n, stub, user, clock, mail):
    who = counterparty()
    overdue_commitment(n8n, stub, user, clock, mail, who)
    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")
    user.post(f"/api/approvals/{proposal['id']}/approve")
    with ThreadPoolExecutor(6) as pool:  # the API's own push, plus five more racing triggers for the same approval
        list(pool.map(lambda _: n8n.call(ACTION, {"approval_id": proposal["id"]}, timeout=120), range(5)))
    wait_until(lambda: approval_status(user, proposal["id"]) == "EXECUTED", what="execution")
    assert stays(lambda: len(mail.messages(who)) == 1, 4)  # exactly one email: the claim is atomic


def test_an_approval_survives_an_n8n_outage_and_runs_when_n8n_is_back(n8n, stub, user, clock, mail, app_db):
    who = counterparty()
    overdue_commitment(n8n, stub, user, clock, mail, who)
    n8n.call(FOLLOWUP_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "SEND_FOLLOW_UP", what="the follow-up proposal")

    compose("stop", "n8n")  # the test project's n8n, never the real one
    try:
        assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200  # the user is not blocked by the outage
        assert approval_status(user, proposal["id"]) == "APPROVED"  # ...and the decision is kept, not lost
        failure = wait_until(
            lambda: app_db.rows("select status, error from automation_runs where workflow_key = 'api-dispatch' and correlation_id = %s", proposal["id"]),
            timeout=40, what="the failed dispatch to be recorded",
        )[0]
        assert failure[0] == "FAILED" and "Could not reach n8n" in failure[1]  # visible, not swallowed
        assert mail.messages(who) == []  # and nothing was sent while n8n was away
    finally:
        compose("start", "n8n")
    wait_for_n8n(n8n.secret)

    assert n8n.call(ACTION, {}, timeout=120).status_code == 200  # the scheduled catch-up (run now instead of waiting up to two minutes)
    sent = wait_until(lambda: mail.messages(who), what="the follow-up after n8n returned")
    assert len(sent) == 1
    wait_until(lambda: approval_status(user, proposal["id"]) == "EXECUTED", what="execution after the outage")


# ------------------------------------------------------------------------------------------------ calendar sync
def test_a_calendar_event_is_proposed_then_created_as_a_real_invite_after_approval(n8n, stub, user, clock, mail, app_db):
    at(clock, T0)
    stub.reset(extraction())
    out = n8n.call("commitmentos-ingest", ingest_payload(user, f"e2e-{uuid.uuid4().hex[:8]}", received_at=T0), timeout=120).json()
    ob_id = out["obligation_id"]
    wait_until(lambda: len(mail.messages(user.email)) == 1, what="the detection email")

    scan = n8n.call(CALENDAR_SCAN, {}, timeout=120).json()
    assert scan["created"] >= 1
    proposal = find_proposal(user, "CREATE_CALENDAR_EVENT", what="the calendar proposal")
    assert proposal["obligation_id"] == ob_id and proposal["payload"]["title"] == "Due: Submit internship documents"
    assert proposal["payload"]["start_at"].startswith("2026-10-02T20:30") and proposal["payload"]["timezone"] == "America/New_York"
    assert app_db.one("select count(*) from calendar_events where obligation_id = %s", ob_id) == 0  # ask first, create after

    assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200
    invite = wait_until(lambda: [m for m in mail.messages(user.email) if m["Subject"].startswith("Calendar invite:")], what="the invite email")[0]
    attachment = mail.detail(invite["ID"])["Attachments"][0]
    assert attachment["FileName"] == "commitment.ics" and attachment["ContentType"].startswith("text/calendar")
    ics = httpx.get(f"{MAILPIT}/api/v1/message/{invite['ID']}/part/{attachment['PartID']}", timeout=10).text
    assert "BEGIN:VEVENT" in ics and "DTSTART:20261002T203000Z" in ics and "DTEND:20261002T210000Z" in ics and f"UID:approval-{proposal['id']}@commitmentos" in ics

    wait_until(lambda: approval_status(user, proposal["id"]) == "EXECUTED", what="execution")
    assert app_db.one("select provider from calendar_events where obligation_id = %s", ob_id) == "LOCAL"
    assert user.get(f"/api/obligations/{ob_id}").json()["obligation"]["status"] == "SCHEDULED"
    n8n.call(CALENDAR_SCAN, {}, timeout=120)
    assert not [p for p in pending(user, "CREATE_CALENDAR_EVENT") if p["obligation_id"] == ob_id]  # asked once


def test_declining_the_calendar_question_is_respected_forever(n8n, stub, user, clock, mail):
    at(clock, T0)
    stub.reset(extraction())
    out = n8n.call("commitmentos-ingest", ingest_payload(user, f"e2e-{uuid.uuid4().hex[:8]}", received_at=T0), timeout=120).json()
    n8n.call(CALENDAR_SCAN, {}, timeout=120)
    proposal = find_proposal(user, "CREATE_CALENDAR_EVENT", what="the calendar proposal")
    assert user.post(f"/api/approvals/{proposal['id']}/reject").status_code == 200
    n8n.call(CALENDAR_SCAN, {}, timeout=120)
    n8n.call(CALENDAR_SCAN, {}, timeout=120)
    assert not [p for p in pending(user, "CREATE_CALENDAR_EVENT") if p["obligation_id"] == out["obligation_id"]]
    assert not [m for m in mail.messages(user.email) if m["Subject"].startswith("Calendar invite:")]


# ------------------------------------------------------------------------------------------------ completion detection
REPLY = "Thanks Alex - we received your signed internship documents this morning. Everything is in order."
EVIDENCE = "we received your signed internship documents"


def test_a_reply_that_says_it_is_done_proposes_completion_and_a_person_confirms(n8n, stub, user, clock, mail, app_db):
    who = counterparty()
    at(clock, T0)
    stub.reset(extraction(counterparty_email=who))
    first = n8n.call("commitmentos-ingest", ingest_payload(user, "e2e-c-1", received_at=T0, sender_email=who, thread_id="thr-c1"), timeout=120).json()
    ob_id = first["obligation_id"]
    wait_until(lambda: len(mail.messages(user.email)) == 1, what="the detection email")
    calls_before = len(stub.calls)

    # the reply: not a new obligation (call 1), but the model is then asked whether it completes the tracked one (call 2)
    stub.reset(
        extraction(is_obligation=False, confidence=0.97, title=None, action=None, deadline_text=None, source_context=None, explanation="An acknowledgement."),
        {"fulfilled": [{"candidate": "C1", "confidence": 0.94, "evidence": EVIDENCE, "explanation": "HR confirms it received the documents."}]},
    )
    reply = n8n.call("commitmentos-ingest", ingest_payload(user, "e2e-c-2", body=REPLY, received_at=T0 + timedelta(hours=3), sender_email=who, thread_id="thr-c1"), timeout=120).json()
    assert reply["outcome"] == "processed" and reply["disposition"] == "NOT_OBLIGATION"

    proposal = find_proposal(user, "COMPLETE_OBLIGATION", what="the completion proposal")
    assert proposal["obligation_id"] == ob_id and proposal["proposed_by"] == "AI" and EVIDENCE in proposal["rationale"]
    assert len(stub.calls) == 2 and calls_before == 1
    assert user.get(f"/api/obligations/{ob_id}").json()["obligation"]["status"] == "OPEN"  # a suggestion changes nothing by itself
    assert run_status(app_db, proposal["automation_run_id"]) == "WAITING"
    wait_for_mail(mail, user, 2, "Approval needed:")

    # the person confirms -> completed, and the deadline that follows brings no reminders
    assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200
    assert user.get(f"/api/obligations/{ob_id}").json()["obligation"]["status"] == "COMPLETED"
    wait_until(lambda: run_status(app_db, proposal["automation_run_id"]) == "SUCCESS", what="the run to resolve")
    at(clock, DUE + timedelta(days=1))
    tick(n8n)
    assert stays(lambda: len(mail.messages(user.email)) == 2, 4)


def test_a_message_that_could_not_complete_anything_never_reaches_the_model_a_second_time(n8n, stub, user, clock, mail):
    at(clock, T0)
    stub.reset(extraction(is_obligation=False, confidence=0.97, title=None, action=None, deadline_text=None, source_context=None, explanation="Chit-chat."))
    r = n8n.call("commitmentos-ingest", ingest_payload(user, "e2e-c-3", body="Lunch on Friday? Let me know!", subject="Lunch", received_at=T0), timeout=120).json()
    assert r["outcome"] == "processed"
    time.sleep(2)
    assert len(stub.calls) == 1  # the extraction only: with nothing tracked, there was nothing to compare against, so no second LLM call


def test_an_untrusted_endpoint_cannot_be_reached_without_the_secret():
    for path in (ACTION, FOLLOWUP_SCAN, CALENDAR_SCAN, "commitmentos-monitor", "commitmentos-dispatch", "commitmentos-completion-check"):
        assert httpx.post(f"{N8N}/webhook/{path}", json={}, timeout=10).status_code == 403
    assert httpx.post(f"{API}/api/internal/monitor/tick", timeout=10).status_code == 401


# ------------------------------------------------------------------------------------------------ an integration that is not connected
def test_google_calendar_selected_but_not_connected_fails_safe_instead_of_pretending(n8n, stub, user, clock, mail, app_db):
    """CALENDAR_PROVIDER=google while no Google account is connected to n8n (the honest state of this environment).
    In n8n a disabled node passes its input straight through, which would look like success - the guard must catch it."""
    restart_api(stub, CALENDAR_PROVIDER="google")
    try:
        at(clock, T0)
        stub.reset(extraction())
        out = n8n.call("commitmentos-ingest", ingest_payload(user, f"e2e-{uuid.uuid4().hex[:8]}", received_at=T0), timeout=120).json()
        n8n.call(CALENDAR_SCAN, {}, timeout=120)
        proposal = find_proposal(user, "CREATE_CALENDAR_EVENT", what="the calendar proposal")
        assert user.post(f"/api/approvals/{proposal['id']}/approve").status_code == 200
        row = wait_until(lambda: (r := user.get(f"/api/approvals/{proposal['id']}").json()) and r["error"] and r, what="the failure to be reported back")
        assert row["status"] in ("APPROVED", "FAILED") and "did not return an event" in row["error"] and row["attempts"] >= 1  # reported, with the reason
        assert user.get(f"/api/obligations/{out['obligation_id']}").json()["obligation"]["status"] == "OPEN"  # NOT marked as scheduled
        assert app_db.one("select count(*) from calendar_events where obligation_id = %s", out["obligation_id"]) == 0  # and no event was recorded
    finally:
        restart_api(stub)
