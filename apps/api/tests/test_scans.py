"""The deterministic scans behind the Calendar Sync and Follow-up Assistant workflows.

They only ever produce *proposals*; whether anything happens is decided by a person. The rules are
plain code, so they are tested as plain code - including the negative cases (who must NOT be asked).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.enums import ApprovalAction, ApprovalStatus, DuePrecision, ObligationType
from app.enums import ObligationStatus as S
from app.models import ApprovalRequest, CalendarEvent, Source, User
from app.services import approvals, calendar_sync, followups
from tests.factories import make_obligation, make_user

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # Thu 08:00 in New York (EDT, UTC-4)
NY = "America/New_York"


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def ny_user(db, email="alice@example.com") -> User:
    return make_user(db, email, timezone=NY, display_name="Alice Example")


def one(db, **kw):
    return calendar_sync.scan(db, NOW, settings, **kw)


# =============================================================================== calendar scan
def test_a_timed_deadline_becomes_a_block_that_ends_at_the_deadline(db):
    user = ny_user(db)
    ob = make_obligation(db, user, title="Submit tax form", due_at=NOW + timedelta(days=2, hours=9))  # Sat 17:00 EDT
    p = one(db)[0]
    assert p.obligation_id == str(ob.id) and p.user_email == "alice@example.com"
    assert p.payload["title"] == "Due: Submit tax form" and p.payload["timezone"] == NY
    assert p.payload["start_at"] == (ob.due_at - timedelta(minutes=30)).isoformat() and p.payload["end_at"] == ob.due_at.isoformat()
    assert approvals.validate_payload(ApprovalAction.CREATE_CALENDAR_EVENT, p.payload)  # exactly what the approval will accept
    assert "Nothing is added until you approve" in p.rationale and str(ob.id) in p.payload["description"]


@pytest.mark.parametrize("kind,minutes", [(ObligationType.INTERVIEW, 60), (ObligationType.APPOINTMENT, 30)])
def test_appointments_start_at_their_time_and_keep_their_own_title(db, kind, minutes):
    user = ny_user(db)
    ob = make_obligation(db, user, title="Dentist checkup", obligation_type=kind, due_at=NOW + timedelta(days=3))
    p = one(db)[0]
    assert p.payload["title"] == "Dentist checkup" and p.payload["start_at"] == ob.due_at.isoformat()
    assert datetime.fromisoformat(p.payload["end_at"]) - ob.due_at == timedelta(minutes=minutes)


def test_an_appointment_with_only_a_date_falls_back_to_the_deadline_rule(db):
    user = ny_user(db)
    make_obligation(db, user, title="Visa interview", obligation_type=ObligationType.INTERVIEW, due_precision=DuePrecision.DATE,
                    due_at=datetime(2026, 9, 30, 3, 59, 59, tzinfo=UTC))  # end of Tue Sep 29 in New York
    p = one(db)[0]
    assert p.payload["title"] == "Due: Visa interview"  # we do not know the time, so we do not invent one
    assert p.payload["end_at"] == datetime(2026, 9, 29, 21, 0, tzinfo=UTC).isoformat()  # 17:00 local: the end of that business day


def test_a_date_only_deadline_ends_at_the_end_of_that_business_day(db):
    user = ny_user(db)
    make_obligation(db, user, due_precision=DuePrecision.DATE, due_at=datetime(2026, 9, 26, 3, 59, 59, tzinfo=UTC))  # Fri Sep 25, end of day
    p = one(db)[0]
    assert p.payload["end_at"] == datetime(2026, 9, 25, 21, 0, tzinfo=UTC).isoformat()


def test_a_date_only_deadline_today_after_office_hours_uses_the_stated_end_of_day(db):
    user = ny_user(db)
    late = datetime(2026, 9, 24, 22, 30, tzinfo=UTC)  # 18:30 local, after the 17:00 close
    make_obligation(db, user, due_precision=DuePrecision.DATE, due_at=datetime(2026, 9, 25, 3, 59, 59, tzinfo=UTC))
    assert calendar_sync.scan(db, late, settings)[0].payload["end_at"] == datetime(2026, 9, 25, 3, 59, 59, tzinfo=UTC).isoformat()


@pytest.mark.parametrize(
    "case,due",
    [
        ("no due date", None),
        ("already past", NOW - timedelta(hours=1)),
        ("due within the hour", NOW + timedelta(minutes=50)),
        ("due in under two hours", NOW + timedelta(hours=1, minutes=59)),
    ],
)
def test_nothing_is_proposed_when_an_event_would_be_pointless(db, case, due):
    make_obligation(db, ny_user(db), due_at=due)
    assert one(db) == [], case


@pytest.mark.parametrize("status", [S.NEEDS_REVIEW, S.COMPLETED, S.DISMISSED, S.SCHEDULED, S.OVERDUE, S.ESCALATED, S.DETECTED])
def test_only_accepted_commitments_that_are_still_ahead_are_proposed(db, status):
    make_obligation(db, ny_user(db), status=status, due_at=NOW + timedelta(days=2))
    assert one(db) == []


@pytest.mark.parametrize("status", [S.OPEN, S.ACTION_REQUIRED])
def test_open_and_action_required_commitments_are_proposed(db, status):
    make_obligation(db, ny_user(db), status=status, due_at=NOW + timedelta(days=2))
    assert len(one(db)) == 1


def test_a_commitment_that_already_has_a_calendar_event_is_left_alone(db):
    user = ny_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=2))
    db.add(CalendarEvent(user_id=user.id, obligation_id=ob.id, provider="LOCAL", external_id="x", title="t", start_at=ob.due_at, end_at=ob.due_at))
    db.flush()
    assert one(db) == []


@pytest.mark.parametrize("status", [ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED])
def test_a_commitment_is_asked_about_once_and_a_no_is_respected(db, status):
    user = ny_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=2))
    db.add(ApprovalRequest(user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.CREATE_CALENDAR_EVENT, status=status, title="t", payload={}))
    db.flush()
    assert one(db) == []


def test_another_kind_of_approval_does_not_block_the_calendar_question(db):
    user = ny_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=2))
    db.add(ApprovalRequest(user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.SEND_FOLLOW_UP, status=ApprovalStatus.PENDING, title="t", payload={}))
    db.flush()
    assert len(one(db)) == 1


def test_results_are_ordered_by_deadline_limited_and_scoped_to_the_right_user(db):
    a, b = ny_user(db, "a@example.com"), make_user(db, "b@example.com", timezone="Asia/Kolkata")
    late = make_obligation(db, a, title="Late", due_at=NOW + timedelta(days=9))
    soon = make_obligation(db, b, title="Soon", due_at=NOW + timedelta(days=2))
    mid = make_obligation(db, a, title="Mid", due_at=NOW + timedelta(days=5))
    got = one(db)
    assert [p.obligation_id for p in got] == [str(soon.id), str(mid.id), str(late.id)]
    assert [p.user_email for p in got] == ["b@example.com", "a@example.com", "a@example.com"]
    assert got[0].payload["timezone"] == "Asia/Kolkata"  # each event is in its owner's timezone
    assert len(one(db, limit=2)) == 2


# =============================================================================== follow-up scan
def scan(db):
    return followups.scan(db, NOW, settings)


def overdue(db, user, **kw):
    kw.setdefault("due_at", NOW - timedelta(days=2))
    kw.setdefault("status", S.OVERDUE)
    kw.setdefault("counterparty_email", "dana@example.org")
    kw.setdefault("counterparty_name", "Dana Whitfield")
    return make_obligation(db, user, title="Send the signed offer", **kw)


def test_something_we_owe_that_is_overdue_gets_a_checkin_draft_for_review(db):
    user = ny_user(db)
    ob = overdue(db, user)
    p = scan(db)[0]
    assert p.obligation_id == str(ob.id) and p.payload["to"] == "dana@example.org"
    assert p.payload["subject"] == "Following up: Send the signed offer"
    assert "Hi Dana," in p.payload["body"] and "where this stands" in p.payload["body"] and p.payload["body"].endswith("Alice Example")
    assert "only sent if you approve" in p.rationale
    assert approvals.validate_payload(ApprovalAction.SEND_FOLLOW_UP, p.payload)


def test_something_someone_else_promised_is_chased_once_it_is_late(db):
    user = ny_user(db)
    make_obligation(db, user, title="Budget report", owner="Priya", counterparty_email="priya@example.org", counterparty_name="Priya Rao",
                    due_at=NOW - timedelta(days=1), status=S.OVERDUE)
    p = scan(db)[0]
    assert p.payload["subject"] == "Checking in: Budget report" and "Could you let me know where it stands?" in p.payload["body"]
    assert "Priya was expected to deliver" in p.rationale
    assert "anything else you need from me" not in p.payload["body"]  # the wording of a nudge, not of an apology


def test_waiting_on_someone_with_no_date_is_chased_only_after_three_days(db):
    user = ny_user(db)
    ob = make_obligation(db, user, title="Contract copy", owner="Priya", counterparty_email="priya@example.org", due_at=None)
    ob.created_at = NOW - timedelta(days=2, hours=23)
    db.flush()
    assert scan(db) == []
    ob.created_at = NOW - timedelta(days=3, minutes=1)
    db.flush()
    assert len(scan(db)) == 1


def test_a_commitment_that_is_not_late_gets_no_followup(db):
    user = ny_user(db)
    make_obligation(db, user, counterparty_email="dana@example.org", due_at=NOW + timedelta(days=1))  # ours, not late
    make_obligation(db, user, owner="Priya", counterparty_email="priya@example.org", due_at=NOW + timedelta(hours=3))  # theirs, not late yet
    overdue(db, user, status=S.OPEN, due_at=NOW + timedelta(days=1))
    assert scan(db) == []


def test_without_a_real_counterparty_there_is_nobody_to_write_to(db):
    user = ny_user(db)
    overdue(db, user, counterparty_email=None)
    overdue(db, user, counterparty_email="ALICE@example.com")  # the user's own address, in any case
    assert scan(db) == []


@pytest.mark.parametrize("status", [S.COMPLETED, S.DISMISSED, S.NEEDS_REVIEW])
def test_closed_or_unreviewed_commitments_are_never_chased(db, status):
    make_obligation(db, ny_user(db), owner="Priya", counterparty_email="priya@example.org", due_at=NOW - timedelta(days=1), status=status)
    assert scan(db) == []


@pytest.mark.parametrize("state", [ApprovalStatus.PENDING, ApprovalStatus.REJECTED, ApprovalStatus.EXECUTED, ApprovalStatus.EXPIRED])
def test_the_same_commitment_is_not_nagged_within_three_days(db, state):
    user = ny_user(db)
    ob = overdue(db, user)
    approval = ApprovalRequest(user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.SEND_FOLLOW_UP, status=state, title="t", payload={})
    db.add(approval)
    db.flush()
    approval.created_at = NOW - timedelta(hours=71)
    db.flush()
    assert scan(db) == []
    approval.created_at = NOW - timedelta(hours=73)
    db.flush()
    assert len(scan(db)) == 1  # after the cooldown a new nudge may be proposed


def test_the_reply_is_threaded_onto_the_original_message(db):
    user = ny_user(db)
    ob = overdue(db, user)
    db.add(Source(user_id=user.id, obligation_id=ob.id, source_type="GMAIL", external_id="g1", thread_id="thr-9", rfc_message_id="<orig@mail.example.org>",
                  disposition="OBLIGATION_CREATED"))
    db.flush()
    p = scan(db)[0]
    assert p.payload["thread_id"] == "thr-9" and p.payload["in_reply_to"] == "<orig@mail.example.org>"


def test_a_confirmation_request_keeps_its_own_wording(db):
    user = ny_user(db)
    ob = overdue(db, user, requires_confirmation=True)
    draft = followups.draft_follow_up(ob, user, NOW, followups.get_zone(NY))
    assert draft["subject"] == "Re: Send the signed offer" and "confirm" in draft["body"]


# =============================================================================== over the wire
def test_scans_require_the_service_secret_and_validate_their_input(client, n8n_headers):
    for path in ("/api/internal/calendar/scan", "/api/internal/followups/scan"):
        assert client.post(path).status_code == 401
        assert client.post(path, headers=n8n_headers, json={"limit": 0}).status_code == 422
        assert client.post(path, headers=n8n_headers, json={"limit": 5, "x": 1}).status_code == 422
        assert client.post(path, headers=n8n_headers).json() == {"items": [], "count": 0}


def test_a_scanned_proposal_round_trips_through_the_webhook_and_is_then_not_proposed_again(client, n8n_headers, db):
    """What the workflow does: scan -> post each item as proposal.created -> the next scan is quiet."""
    user = ny_user(db)
    make_obligation(db, user, title="Submit tax form", due_at=NOW + timedelta(days=2))
    overdue(db, user)
    db.commit()
    for path, action in (("/api/internal/calendar/scan", "CREATE_CALENDAR_EVENT"), ("/api/internal/followups/scan", "SEND_FOLLOW_UP")):
        items = client.post(path, headers=n8n_headers).json()["items"]
        assert len(items) >= 1 and all(i["action_type"] == action for i in items)
        for item in items:
            body = {"event": "proposal.created", "obligation_id": item["obligation_id"], "action_type": item["action_type"], "title": item["title"],
                    "rationale": item["rationale"], "payload": item["payload"], "proposed_by": "SYSTEM"}
            assert client.post("/api/webhooks/n8n", json=body, headers=n8n_headers).json()["created"] is True
        assert client.post(path, headers=n8n_headers).json()["count"] == 0
    assert {a.status for a in db.scalars(select(ApprovalRequest))} == {ApprovalStatus.PENDING}  # nothing was executed by any of this
