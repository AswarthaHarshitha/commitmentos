"""A follow-up goes to the address the original email came from - and to nowhere else, however it is asked for.

The rule lives in one function (`followups.follow_up_target`) and is enforced when a follow-up is drafted, when a workflow proposes
one, when a person edits one, and again when n8n is about to send it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.clock import clock
from app.config import get_settings
from app.enums import ApprovalStatus, ObligationStatus, SourceDisposition, SourceType
from app.models import ApprovalRequest, Source
from app.services import approvals, followups
from tests.factories import make_obligation, make_user
from tests.helpers_extraction import make_extraction, make_message

NOW = datetime(2026, 9, 23, 15, 5, tzinfo=UTC)
settings = get_settings()


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def ingest(client, headers, *, message=None, extraction=None, execution="exec-1"):
    event = {
        "event": "message.extracted", "user_email": "alice@example.com", "n8n_execution_id": execution,
        "message": make_message(**(message or {})), "extraction": make_extraction(**(extraction or {})),
    }
    r = client.post("/api/webhooks/n8n", json=event, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["obligation_id"]


def obligation(client, oid):
    return client.get(f"/api/obligations/{oid}").json()


def draft(client, oid):
    return client.post(f"/api/obligations/{oid}/follow-up")


# ------------------------------------------------------------------ what gets recorded from a message
def test_the_address_recorded_is_the_senders_even_when_the_text_names_another(alice, n8n_headers):
    oid = ingest(alice, n8n_headers, extraction={"counterparty_email": "registrar@school.edu", "counterparty_name": "The Registrar"})
    ob = obligation(alice, oid)["obligation"]
    assert ob["counterparty_email"] == "hr@example.org"  # from the message header, not from the words in it
    assert ob["counterparty_name"] == "Dana Whitfield"  # and the name that belongs to that address, not the other one's


def test_a_follow_up_is_addressed_to_the_original_sender(alice, n8n_headers):
    oid = ingest(alice, n8n_headers, extraction={"counterparty_email": "registrar@school.edu"})
    r = draft(alice, oid)
    assert r.status_code == 201 and r.json()["payload"]["to"] == "hr@example.org"
    d = obligation(alice, oid)
    assert d["follow_up_to"] == "hr@example.org" and d["follow_up_to_original"] is True


def test_editing_the_commitments_address_cannot_redirect_a_follow_up(alice, n8n_headers):
    oid = ingest(alice, n8n_headers)
    assert alice.patch(f"/api/obligations/{oid}", json={"counterparty_email": "someone@else.org"}).status_code == 200
    assert draft(alice, oid).json()["payload"]["to"] == "hr@example.org"


def test_the_persons_own_address_is_never_a_recipient(alice, n8n_headers):
    oid = ingest(alice, n8n_headers, message={"sender_email": "alice@example.com", "external_id": "own-1"})
    assert obligation(alice, oid)["obligation"]["counterparty_email"] is None
    r = draft(alice, oid)
    assert r.status_code == 422 and "no address to send a follow-up to" in r.json()["detail"]


def test_an_email_with_no_sender_needs_an_address_before_it_can_be_followed_up(alice, n8n_headers):
    oid = ingest(alice, n8n_headers, message={"sender_email": None, "sender_name": None, "external_id": "pasted-1"})
    assert obligation(alice, oid)["follow_up_to"] is None and draft(alice, oid).status_code == 422
    assert alice.patch(f"/api/obligations/{oid}", json={"counterparty_email": "Dana@Example.org"}).status_code == 200
    d = obligation(alice, oid)
    assert d["follow_up_to"] == "dana@example.org" and d["follow_up_to_original"] is False  # given by hand, and shown as such
    assert draft(alice, oid).json()["payload"]["to"] == "dana@example.org"


@pytest.mark.parametrize(
    ("recipients", "named", "expected"),
    [
        (["marcus@example.org"], None, "marcus@example.org"),
        (["alice@example.com", "marcus@example.org"], None, "marcus@example.org"),  # the person's own address is not "the other party"
        (["a@example.org", "b@example.org"], "b@example.org", "b@example.org"),  # several: the one the model named, if it is really there
        (["a@example.org", "b@example.org"], "c@example.org", None),  # named, but not among the recipients: not trusted
        (["a@example.org", "b@example.org"], None, None),  # several and no way to tell: ask
        ([], "marcus@example.org", None),  # no recipients recorded: the model's guess alone is not enough
    ],
)
def test_for_an_email_the_person_sent_the_address_comes_from_who_it_was_sent_to(alice, n8n_headers, recipients, named, expected):
    message = {"sender_email": "alice@example.com", "direction": "OUTBOUND", "recipients": recipients, "external_id": f"out-{len(recipients)}-{named}"}
    oid = ingest(alice, n8n_headers, message=message, extraction={"owner": "OTHER", "counterparty_email": named, "counterparty_name": "Marcus"})
    assert obligation(alice, oid)["obligation"]["counterparty_email"] == expected


# ------------------------------------------------------------------ every way of asking is held to the same rule
def test_a_proposal_from_a_workflow_addressed_elsewhere_is_refused_and_nothing_is_stored(alice, n8n_headers, db):
    oid = ingest(alice, n8n_headers)
    event = {"event": "proposal.created", "obligation_id": oid, "action_type": "SEND_FOLLOW_UP", "title": "Follow up", "proposed_by": "AI",
             "n8n_execution_id": "exec-2", "payload": {"to": "attacker@evil.example", "subject": "s", "body": "b"}}
    r = alice.post("/api/webhooks/n8n", json=event, headers=n8n_headers)
    assert r.status_code == 422 and "hr@example.org" in r.json()["detail"]
    assert db.scalar(select(ApprovalRequest.id)) is None


def test_a_proposal_addressed_to_the_original_sender_is_accepted_whatever_its_letter_case(alice, n8n_headers):
    oid = ingest(alice, n8n_headers)
    event = {"event": "proposal.created", "obligation_id": oid, "action_type": "SEND_FOLLOW_UP", "title": "Follow up", "proposed_by": "AI",
             "n8n_execution_id": "exec-2", "payload": {"to": "HR@Example.org", "subject": "s", "body": "b"}}
    assert alice.post("/api/webhooks/n8n", json=event, headers=n8n_headers).json()["created"] is True


def test_the_recipient_of_a_draft_cannot_be_edited(alice, n8n_headers):
    oid = ingest(alice, n8n_headers)
    approval = draft(alice, oid).json()
    assert alice.patch(f"/api/approvals/{approval['id']}", json={"to": "attacker@evil.example"}).status_code == 422
    assert alice.patch(f"/api/approvals/{approval['id']}", json={"subject": "A better subject"}).json()["payload"]["to"] == "hr@example.org"


def test_a_draft_that_no_longer_matches_is_not_handed_to_n8n(alice, n8n_headers, db):
    oid = ingest(alice, n8n_headers)
    approval = draft(alice, oid).json()
    assert alice.post(f"/api/approvals/{approval['id']}/approve").status_code == 200
    db.execute(update(Source).values(sender_email="changed@example.org"))  # whatever happens between approving and sending
    db.commit()
    assert approvals.claim(db, NOW, settings) == []
    db.commit()
    db.expire_all()
    stored = db.get(ApprovalRequest, approval["id"])
    assert stored.status == ApprovalStatus.FAILED and "no longer the address the original email came from" in stored.error


def test_a_matching_approved_draft_is_handed_to_n8n(alice, n8n_headers, db):
    oid = ingest(alice, n8n_headers)
    approval = draft(alice, oid).json()
    alice.post(f"/api/approvals/{approval['id']}/approve")
    assert [a.payload["to"] for a in approvals.claim(db, NOW, settings)] == ["hr@example.org"]


# ------------------------------------------------------------------ the Follow-up Assistant's own scan
def _overdue_from(db, user, sender, title):
    ob = make_obligation(db, user, title=title, status=ObligationStatus.OVERDUE, due_at=NOW - timedelta(days=1))
    if sender:
        db.add(Source(user_id=user.id, obligation_id=ob.id, source_type=SourceType.GMAIL, external_id=f"m-{title}", sender_email=sender,
                      disposition=SourceDisposition.OBLIGATION_CREATED, role="PRIMARY", created_at=NOW, updated_at=NOW))
    return ob


def test_the_scan_proposes_the_original_sender_and_skips_commitments_with_nobody_to_write_to(db):
    user = make_user(db, "alice@example.com")
    for i in range(3):
        _overdue_from(db, user, None, f"nobody-{i}")  # older, so a naive LIMIT would spend itself on these
    ob = _overdue_from(db, user, "Dana@Example.org", "reachable")
    ob.counterparty_email = "someone-else@example.org"  # what the commitment records does not matter when an original email exists
    db.commit()
    proposals = followups.scan(db, NOW, settings, limit=1)
    assert [p.payload["to"] for p in proposals] == ["dana@example.org"]


def test_a_commitment_added_by_hand_uses_the_address_it_was_given(db):
    user = make_user(db, "alice@example.com")
    ob = make_obligation(db, user, counterparty_email="Sam@Example.org")
    db.commit()
    target = followups.follow_up_target(db, ob, user)
    assert (target.address, target.from_original_email) == ("sam@example.org", False)


def test_a_commitment_added_by_hand_never_uses_the_persons_own_address(db):
    user = make_user(db, "alice@example.com")
    ob = make_obligation(db, user, counterparty_email="ALICE@example.com")
    db.commit()
    assert followups.follow_up_target(db, ob, user).address is None


def test_the_original_email_wins_over_a_duplicate_seen_later(db):
    user = make_user(db, "alice@example.com")
    ob = make_obligation(db, user, counterparty_email="x@example.org")
    later = NOW + timedelta(hours=1)
    db.add(Source(user_id=user.id, obligation_id=ob.id, source_type=SourceType.GMAIL, external_id="dup", sender_email="reminder@example.org",
                  disposition=SourceDisposition.DUPLICATE, role="DUPLICATE", created_at=NOW, updated_at=NOW))
    db.add(Source(user_id=user.id, obligation_id=ob.id, source_type=SourceType.GMAIL, external_id="orig", sender_email="dana@example.org",
                  disposition=SourceDisposition.OBLIGATION_CREATED, role="PRIMARY", created_at=later, updated_at=later))
    db.commit()
    assert followups.follow_up_target(db, ob, user).address == "dana@example.org"
