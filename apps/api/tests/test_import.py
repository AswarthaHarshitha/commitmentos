"""Importing an email a person pasted in: queued for n8n, recognised when repeated, answered from the record, failures visible."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.enums import RunStatus
from app.models import AutomationRun, Source
from app.services import importing
from tests.helpers_extraction import BODY, make_extraction, make_message

NOW = datetime(2026, 9, 23, 15, 5, tzinfo=UTC)
PASTED = {"subject": "Internship paperwork", "sender": "Dana Whitfield <hr@example.org>", "body": BODY}


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def do_import(client, **over):
    return client.post("/api/messages/import", json={**PASTED, **over})


def workflow_reports(client, headers, external_id, *, extraction=None, **over):
    """What the detection workflow does when it finishes: report the message it read."""
    event = {
        "event": "message.extracted", "user_email": "alice@example.com", "n8n_execution_id": f"exec-{external_id[-6:]}",
        "message": make_message(source_type="IMPORTED", external_id=external_id),
        "extraction": make_extraction(**(extraction or {})), **over,
    }
    r = client.post("/api/webhooks/n8n", json=event, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def outcome(client, external_id, **params):
    r = client.get(f"/api/messages/import/{external_id}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ queueing
def test_importing_needs_a_signed_in_user(client):
    assert do_import(client).status_code == 401


def test_an_import_is_queued_for_the_detection_workflow_and_answers_at_once(alice, fake_n8n):
    r = do_import(alice)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "processing" and body["external_id"].startswith("import-") and body["requested_at"] == "2026-09-23T15:05:00Z"

    (path, payload), = fake_n8n.calls
    assert path == "commitmentos-ingest"
    assert payload["user_email"] == "alice@example.com"
    assert payload["message"] == {
        "source_type": "IMPORTED", "external_id": body["external_id"], "sender_email": "hr@example.org", "sender_name": "Dana Whitfield",
        "subject": "Internship paperwork", "body": BODY, "received_at": "2026-09-23T15:05:00+00:00", "direction": "INBOUND",
    }
    # the detection workflow answers only when it has finished: long wait, and no retry while it is still reading
    assert fake_n8n.options["timeout"] >= 120 and fake_n8n.options["attempts"] == 1


def test_the_time_the_email_arrived_is_kept_because_relative_deadlines_are_read_from_it(alice, fake_n8n):
    do_import(alice, received_at="2026-09-20T08:00:00Z")
    assert fake_n8n.calls[0][1]["message"]["received_at"] == "2026-09-20T08:00:00+00:00"


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        ("Dana Whitfield <dana@example.org>", ("Dana Whitfield", "dana@example.org")),
        ("dana@example.org", (None, "dana@example.org")),
        ('"Whitfield, Dana" <Dana@Example.org>', ("Whitfield, Dana", "Dana@example.org")),
        ("Dana at the front desk", ("Dana at the front desk", None)),
        ("<not-an-address>", ("<not-an-address>", None)),
        ("   ", (None, None)),
        (None, (None, None)),
    ],
)
def test_the_sender_is_split_into_a_name_and_an_address(sender, expected):
    assert importing.split_sender(sender) == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"body": ""},
        {"body": "   "},
        {"body": "x" * 100_001},
        {"subject": "s" * 501},
        {"received_at": "2026-09-23T15:05:00"},  # which moment is that?
        {"received_at": "2026-09-24T15:05:00Z"},  # tomorrow: an email cannot have arrived yet
        {"surprise": 1},
    ],
)
def test_bad_input_is_refused_and_nothing_is_queued(alice, fake_n8n, bad):
    assert do_import(alice, **bad).status_code == 422
    assert fake_n8n.calls == []


def test_importing_is_rate_limited_per_person(alice, fake_n8n):
    codes = [do_import(alice, body=f"Please send report {i} by Friday").status_code for i in range(21)]
    assert codes[:20] == [202] * 20 and codes[20] == 429


# ------------------------------------------------------------------ the outcome
def test_the_outcome_is_processing_until_the_workflow_has_reported(alice, fake_n8n):
    ext = do_import(alice).json()["external_id"]
    assert outcome(alice, ext)["status"] == "processing"


def test_once_the_workflow_reports_the_outcome_is_the_commitment_it_created(alice, fake_n8n, n8n_headers):
    ext = do_import(alice).json()["external_id"]
    created = workflow_reports(alice, n8n_headers, ext)
    got = outcome(alice, ext)
    assert got["status"] == "done" and got["disposition"] == "OBLIGATION_CREATED"
    assert got["obligation_id"] == created["obligation_id"] and got["title"] == "Submit internship documents" and got["obligation_status"] == "OPEN"
    assert alice.get(f"/api/obligations/{created['obligation_id']}").json()["obligation"]["source"] == "IMPORTED"


def test_the_same_email_is_recognised_and_not_read_again(alice, fake_n8n, n8n_headers):
    ext = do_import(alice).json()["external_id"]
    workflow_reports(alice, n8n_headers, ext)
    again = do_import(alice)
    assert again.status_code == 200 and again.json()["status"] == "done" and again.json()["external_id"] == ext
    assert len(fake_n8n.calls) == 1  # no second run, so no second model call


def test_different_text_is_a_different_email(alice, fake_n8n):
    a = do_import(alice).json()["external_id"]
    b = do_import(alice, body=BODY + " P.S. bring your passport").json()["external_id"]
    assert a != b and len(fake_n8n.calls) == 2


def test_the_same_text_from_two_people_is_two_emails(alice, bob, fake_n8n):
    assert do_import(alice).json()["external_id"] != do_import(bob).json()["external_id"]


def test_a_low_confidence_read_is_reported_as_a_candidate_with_its_title(alice, fake_n8n, n8n_headers):
    ext = do_import(alice).json()["external_id"]
    workflow_reports(alice, n8n_headers, ext, extraction={"confidence": 0.45, "title": "Maybe send the documents"})
    got = outcome(alice, ext)
    assert got["status"] == "done" and got["disposition"] == "CANDIDATE" and got["title"] == "Maybe send the documents" and got["obligation_id"] is None


def test_an_email_with_no_commitment_is_reported_as_such(alice, fake_n8n, n8n_headers):
    ext = do_import(alice, body="Thanks for lunch yesterday, it was lovely.").json()["external_id"]
    workflow_reports(alice, n8n_headers, ext, extraction={"is_obligation": False, "confidence": 0.9, "title": None, "action": None, "deadline_text": None, "source_context": None})
    got = outcome(alice, ext)
    assert got["status"] == "done" and got["disposition"] == "NOT_OBLIGATION" and got["title"] is None


def test_nobody_can_see_the_outcome_of_someone_elses_import(alice, bob, fake_n8n, n8n_headers):
    ext = do_import(alice).json()["external_id"]
    workflow_reports(alice, n8n_headers, ext)
    assert outcome(bob, ext) == {**outcome(bob, "import-unknown"), "external_id": ext}  # exactly what an unknown id looks like


# ------------------------------------------------------------------ failures are visible, and a failed read can be retried
def test_a_failed_read_is_reported_with_a_reason_a_person_can_act_on(alice, fake_n8n, n8n_headers):
    ext = do_import(alice).json()["external_id"]
    workflow_reports(alice, n8n_headers, ext, extraction=None, extraction_status="LLM_QUOTA_EXHAUSTED", extraction_error="429")
    got = outcome(alice, ext)
    assert got["status"] == "failed" and got["disposition"] == "EXTRACTION_FAILED" and "usage limit" in got["detail"]


def test_a_failed_read_can_be_imported_again_and_only_the_new_attempt_answers(alice, fake_n8n, n8n_headers, db):
    ext = do_import(alice).json()["external_id"]
    workflow_reports(alice, n8n_headers, ext, extraction=None, extraction_status="LLM_UNAVAILABLE", extraction_error="503")
    assert db.scalar(select(Source.disposition)).value == "EXTRACTION_FAILED"

    clock.freeze(NOW + timedelta(minutes=1))
    retry = do_import(alice)
    assert retry.status_code == 202 and len(fake_n8n.calls) == 2  # read again
    since = retry.json()["requested_at"]
    assert outcome(alice, ext, since=since)["status"] == "processing"  # the earlier failure is not this attempt's answer

    clock.freeze(NOW + timedelta(minutes=2))
    workflow_reports(alice, n8n_headers, ext, n8n_execution_id="exec-retry")
    got = outcome(alice, ext, since=since)
    assert got["status"] == "done" and got["disposition"] == "OBLIGATION_CREATED"


def test_an_engine_that_cannot_be_reached_is_reported_not_swallowed(alice, fake_n8n, db):
    fake_n8n.fail_with = "ConnectError: n8n unreachable"
    r = do_import(alice)
    assert r.status_code == 202
    ext, since = r.json()["external_id"], r.json()["requested_at"]

    run = db.scalar(select(AutomationRun).where(AutomationRun.correlation_id == ext))
    assert run is not None and run.status == RunStatus.FAILED and "could not be handed" in run.error and run.workflow_key == "api-dispatch"
    got = outcome(alice, ext, since=since)
    assert got["status"] == "failed" and "Import it again" in got["detail"]

    fake_n8n.fail_with = None
    clock.freeze(NOW + timedelta(minutes=1))
    retry = do_import(alice)  # nothing was recorded as read, so the same email is simply queued again
    assert retry.status_code == 202 and outcome(alice, ext, since=retry.json()["requested_at"])["status"] == "processing"
