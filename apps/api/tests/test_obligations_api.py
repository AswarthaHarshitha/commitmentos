"""Obligation API behaviour: creation, timezones, validation, actions, idempotency, recurrence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.enums import ObligationStatus as S
from app.models import AuditEvent, Notification, Obligation, User
from app.services import monitor
from tests.factories import make_obligation

# 2026-09-24 12:00 UTC == 08:00 in New York (EDT)
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def create(client, **over):
    body = {"title": "Submit tax form", "due": {"date": "2026-09-30", "time": "17:00"}}
    body.update(over)
    r = client.post("/api/obligations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_manual_obligation_local_deadline_is_stored_as_utc_using_the_users_timezone(alice):
    ob = create(alice)  # Alice is in America/New_York: 17:00 EDT
    assert ob["due_at"] == "2026-09-30T21:00:00Z" and ob["due_precision"] == "DATETIME"
    assert ob["status"] == "OPEN" and ob["source"] == "MANUAL" and ob["confidence"] == 1.0
    assert ob["acknowledged_at"] is not None  # a manual entry is never an "unseen" detection
    assert ob["due_timezone"] == "America/New_York"


def test_date_only_deadline_is_end_of_the_local_day(alice):
    ob = create(alice, due={"date": "2026-09-30"})
    assert ob["due_precision"] == "DATE" and ob["due_at"] == "2026-10-01T03:59:59Z"  # 23:59:59 EDT


def test_absolute_deadline_with_offset_is_normalised_to_utc(alice):
    ob = create(alice, due=None, due_at="2026-09-30T17:00:00+05:30")
    assert ob["due_at"] == "2026-09-30T11:30:00Z"


def test_dst_gap_time_is_shifted_and_the_user_is_told_why(alice):
    ob = create(alice, due={"date": "2026-11-01", "time": "01:30"})
    assert ob["due_at"] == "2026-11-01T05:30:00Z"  # first occurrence of the ambiguous 01:30
    assert any("twice" in w for w in ob["due_resolution"]["warnings"])
    gap = create(alice, title="Other", due={"date": "2027-03-14", "time": "02:30"})
    assert gap["due_at"] == "2027-03-14T07:30:00Z" and any("does not exist" in w for w in gap["due_resolution"]["warnings"])


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"due": None, "due_at": "2026-09-30T17:00:00"}, "UTC offset"),  # naive timestamps are ambiguous -> refused
        ({"due_at": "2026-09-30T17:00:00Z"}, "either"),  # both forms at once
        ({"title": "   "}, "blank"),
        ({"title": ""}, "at least 1"),
        ({"title": "x" * 301}, "at most 300"),
        ({"priority": "SUPER"}, "priority"),
        ({"obligation_type": "MEETING"}, "obligation_type"),
        ({"status": "COMPLETED"}, "status"),  # status is never client-settable
        ({"user_id": str(uuid.uuid4())}, "user_id"),  # mass assignment
        ({"confidence": 0.1}, "confidence"),
        ({"due": {"date": "2026-02-31"}}, "date"),
        ({"due": {"date": "2026-09-30", "time": "25:00"}}, "time"),
        ({"counterparty_email": "nope"}, "counterparty_email"),
    ],
)
def test_invalid_creation_requests_are_rejected_with_422(alice, override, needle):
    body = {"title": "Submit tax form", "due": {"date": "2026-09-30"}, **override}
    r = alice.post("/api/obligations", json=body)
    assert r.status_code == 422, r.text
    assert needle.lower() in r.text.lower()


def test_creation_is_audited_and_scheduled_for_monitoring(alice, db):
    ob = create(alice)
    row = db.get(Obligation, uuid.UUID(ob["id"]))
    assert row.next_action_at == row.due_at - timedelta(hours=24)
    ev = db.scalar(select(AuditEvent).where(AuditEvent.obligation_id == row.id))
    assert ev.message == "Commitment added by you" and ev.actor_type.value == "USER"


def test_list_filters_search_sort_and_pagination(alice, db):
    a = create(alice, title="Alpha report", due={"date": "2026-10-03"}, priority="HIGH")
    b = create(alice, title="Beta 100% done", due={"date": "2026-10-01"})
    c = create(alice, title="Gamma no deadline", due=None, obligation_type="TASK")
    listing = alice.get("/api/obligations").json()
    assert [o["title"] for o in listing["items"]] == ["Beta 100% done", "Alpha report", "Gamma no deadline"]  # soonest first, undated last
    assert listing["total"] == 3
    assert [o["title"] for o in alice.get("/api/obligations?q=alpha").json()["items"]] == ["Alpha report"]
    assert [o["title"] for o in alice.get("/api/obligations", params={"q": "100%"}).json()["items"]] == ["Beta 100% done"]
    assert alice.get("/api/obligations", params={"q": "%"}).json()["total"] == 1  # '%' is literal, not a wildcard
    assert [o["id"] for o in alice.get("/api/obligations?priority=HIGH").json()["items"]] == [a["id"]]
    assert [o["id"] for o in alice.get("/api/obligations?has_due=false").json()["items"]] == [c["id"]]
    page = alice.get("/api/obligations?limit=1&offset=1").json()
    assert page["total"] == 3 and [o["id"] for o in page["items"]] == [a["id"]]
    assert alice.get("/api/obligations?sort=created").json()["items"][0]["id"] == c["id"]  # newest first
    assert b["id"] in [o["id"] for o in alice.get("/api/obligations?view=upcoming").json()["items"]]


@pytest.mark.parametrize("query", ["view=nonsense", "status=BOGUS", "limit=0", "limit=500", "offset=-1", "sort=random", "due_before=2026-01-01T00:00:00"])
def test_list_rejects_bad_query_parameters(alice, query):
    assert alice.get(f"/api/obligations?{query}").status_code == 422


def test_views_partition_obligations_correctly(alice, db):
    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    make_obligation(db, user, title="review me", status=S.NEEDS_REVIEW, due_at=NOW + timedelta(days=2))
    make_obligation(db, user, title="late", status=S.OVERDUE, due_at=NOW - timedelta(days=1), acknowledged_at=NOW)
    make_obligation(db, user, title="done", status=S.COMPLETED, acknowledged_at=NOW)
    make_obligation(db, user, title="tracked", status=S.OPEN, due_at=NOW + timedelta(days=3), acknowledged_at=NOW)
    db.commit()
    titles = lambda v: sorted(o["title"] for o in alice.get(f"/api/obligations?view={v}").json()["items"])  # noqa: E731
    assert titles("inbox") == ["review me"]
    assert titles("review") == ["review me"]
    assert titles("overdue") == ["late"]
    assert titles("closed") == ["done"]
    assert titles("active") == ["late", "tracked"]
    assert titles("upcoming") == ["tracked"]


def test_patch_edits_fields_and_reports_what_changed(alice):
    ob = create(alice)
    r = alice.patch(f"/api/obligations/{ob['id']}", json={"title": "File tax form", "priority": "URGENT"}).json()
    assert r["changed"] is True and r["obligation"]["title"] == "File tax form" and r["obligation"]["priority"] == "URGENT"
    same = alice.patch(f"/api/obligations/{ob['id']}", json={"title": "File tax form"}).json()
    assert same["changed"] is False


def test_changing_the_deadline_cancels_old_reminders_and_reevaluates_the_status(alice, db):
    ob = create(alice, due={"date": "2026-09-25", "time": "09:00"})  # due in ~25h
    row = db.get(Obligation, uuid.UUID(ob["id"]))
    user = db.get(User, row.user_id)
    clock.freeze(row.due_at - timedelta(hours=24))
    monitor.run_tick(db, clock.now(), get_settings())
    db.commit()
    assert db.scalars(select(Notification).where(Notification.status == "PENDING")).all()  # an email reminder is queued
    db.refresh(row)
    assert row.status == S.ACTION_REQUIRED

    r = alice.patch(f"/api/obligations/{ob['id']}", json={"due": {"date": "2026-10-20", "time": "09:00"}}).json()
    assert r["obligation"]["status"] == "OPEN"  # back out of the reminder window
    assert r["obligation"]["due_resolution"]["method"] == "USER_EDIT"
    db.expire_all()
    cancelled = db.scalars(select(Notification).where(Notification.status == "CANCELLED")).all()
    assert cancelled  # the now-wrong reminder will never be delivered
    assert db.get(Obligation, row.id).next_action_at > clock.now()
    assert user is not None


def test_clearing_the_deadline_stops_monitoring(alice, db):
    ob = create(alice)
    r = alice.patch(f"/api/obligations/{ob['id']}", json={"clear_due": True}).json()["obligation"]
    assert r["due_at"] is None and r["next_action_at"] is None and r["due_precision"] is None


def test_editing_a_closed_obligation_is_a_conflict_and_bad_patches_are_422(alice):
    ob = create(alice)
    alice.post(f"/api/obligations/{ob['id']}/complete")
    assert alice.patch(f"/api/obligations/{ob['id']}", json={"title": "x"}).status_code == 409
    fresh = create(alice, title="Fresh")
    for body in ({"title": ""}, {"unknown": 1}, {"status": "OPEN"}, {"clear_due": True, "due": {"date": "2026-10-01"}}):
        assert alice.patch(f"/api/obligations/{fresh['id']}", json=body).status_code == 422, body


def test_complete_is_idempotent_and_audited_once(alice, db):
    ob = create(alice)
    first = alice.post(f"/api/obligations/{ob['id']}/complete", json={"note": "sent by post"}).json()
    second = alice.post(f"/api/obligations/{ob['id']}/complete").json()
    assert first["changed"] is True and second["changed"] is False
    assert first["obligation"]["status"] == "COMPLETED" and first["obligation"]["completed_via"] == "DASHBOARD"
    assert first["obligation"]["completed_at"] == "2026-09-24T12:00:00Z" and first["obligation"]["next_action_at"] is None
    completed = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "COMPLETED")).all()
    assert len(completed) == 1 and completed[0].data["note"] == "sent by post"


def test_completing_a_dismissed_obligation_is_refused_until_it_is_reopened(alice):
    ob = create(alice)
    alice.post(f"/api/obligations/{ob['id']}/dismiss", json={"reason": "not mine"})
    r = alice.post(f"/api/obligations/{ob['id']}/complete")
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"
    assert alice.post(f"/api/obligations/{ob['id']}/reopen").json()["obligation"]["status"] == "OPEN"
    assert alice.post(f"/api/obligations/{ob['id']}/complete").json()["changed"] is True


def test_dismissing_a_completed_obligation_is_refused_and_reopen_only_works_on_closed_ones(alice):
    ob = create(alice)
    alice.post(f"/api/obligations/{ob['id']}/complete")
    assert alice.post(f"/api/obligations/{ob['id']}/dismiss").status_code == 409
    open_one = create(alice, title="Still open")
    assert alice.post(f"/api/obligations/{open_one['id']}/reopen").status_code == 409


def test_snooze_variants_and_limits(alice):
    ob = create(alice)
    r = alice.post(f"/api/obligations/{ob['id']}/snooze", json={"hours": 6}).json()["obligation"]
    assert r["snoozed_until"] == "2026-09-24T18:00:00Z"
    assert alice.post(f"/api/obligations/{ob['id']}/snooze", json={"until": "2026-09-25T09:00:00-04:00"}).json()["obligation"]["snoozed_until"] == "2026-09-25T13:00:00Z"
    assert alice.post(f"/api/obligations/{ob['id']}/snooze").json()["obligation"]["snoozed_until"] == "2026-09-24T16:00:00Z"  # default 4h
    for bad in ({"hours": 0}, {"hours": 24 * 31}, {"until": "2026-09-24T11:00:00Z"}, {"until": "2026-09-25T09:00:00"}, {"hours": 2, "until": "2026-09-25T09:00:00Z"}, {"until": "2027-01-01T00:00:00Z"}):
        assert alice.post(f"/api/obligations/{ob['id']}/snooze", json=bad).status_code == 422, bad
    alice.post(f"/api/obligations/{ob['id']}/complete")
    assert alice.post(f"/api/obligations/{ob['id']}/snooze", json={"hours": 2}).status_code == 409


def test_accepting_a_detection_makes_it_active_and_acknowledged(alice, db):
    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    row = make_obligation(db, user, status=S.NEEDS_REVIEW, due_at=NOW + timedelta(days=5), confidence=0.68)
    db.commit()
    r = alice.post(f"/api/obligations/{row.id}/approve").json()
    assert r["changed"] is True and r["obligation"]["status"] == "OPEN" and r["obligation"]["acknowledged_at"] is not None
    assert alice.post(f"/api/obligations/{row.id}/approve").json()["changed"] is False  # idempotent
    assert alice.get("/api/obligations?view=inbox").json()["total"] == 0


def test_accepting_an_item_that_expects_a_reply_marks_it_action_required(alice, db):
    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    row = make_obligation(db, user, status=S.NEEDS_REVIEW, due_at=NOW + timedelta(days=5), requires_confirmation=True)
    db.commit()
    assert alice.post(f"/api/obligations/{row.id}/approve").json()["obligation"]["status"] == "ACTION_REQUIRED"


def test_recurring_obligation_spawns_exactly_one_next_occurrence_when_completed(alice, db):
    ob = create(alice, title="Pay rent", due={"date": "2026-10-01", "time": "09:00"}, recurrence="MONTHLY")
    done = alice.post(f"/api/obligations/{ob['id']}/complete").json()
    nxt = done["spawned_next"]
    # Oct 1 09:00 EDT was 13:00Z. DST ends at 02:00 on Nov 1, so 09:00 that morning is EST: the wall-clock
    # time is kept (09:00) while the UTC instant moves an hour (14:00Z).
    assert ob["due_at"] == "2026-10-01T13:00:00Z"
    assert nxt["due_at"] == "2026-11-01T14:00:00Z"
    assert nxt["status"] == "OPEN" and nxt["recurrence"] == "MONTHLY" and nxt["id"] != ob["id"]
    again = alice.post(f"/api/obligations/{ob['id']}/complete").json()
    assert again["spawned_next"] is None  # completing twice must not spawn twice
    assert alice.get("/api/obligations").json()["total"] == 2


def test_long_overdue_recurring_item_catches_up_instead_of_spawning_in_the_past(alice, db):
    user = db.scalar(select(User).where(User.email == "alice@example.com"))
    row = make_obligation(db, user, status=S.ESCALATED, due_at=NOW - timedelta(days=100), recurrence="MONTHLY")
    db.commit()
    nxt = alice.post(f"/api/obligations/{row.id}/complete").json()["spawned_next"]
    assert datetime.fromisoformat(nxt["due_at"].replace("Z", "+00:00")) > NOW


def test_detail_explains_a_manual_obligation_and_suggests_the_next_step(alice):
    ob = create(alice, title="Renew passport", due={"date": "2026-09-24", "time": "17:00"})
    detail = alice.get(f"/api/obligations/{ob['id']}").json()
    assert detail["understanding"]["origin"] == "MANUAL" and "created this commitment yourself" in detail["understanding"]["explanation"]
    assert detail["suggested_next_action"]["code"] == "DO_IT_NOW"  # due in 9 hours
    assert detail["timezone"] == "America/New_York" and detail["now"] == "2026-09-24T12:00:00Z"
    far = create(alice, title="Far away", due={"date": "2026-12-01"})
    assert alice.get(f"/api/obligations/{far['id']}").json()["suggested_next_action"]["code"] == "WAIT"
    undated = create(alice, title="Undated", due=None)
    assert alice.get(f"/api/obligations/{undated['id']}").json()["suggested_next_action"]["code"] == "SET_DEADLINE"


def test_timeline_shows_what_happened_and_what_the_system_will_do_next(alice):
    ob = create(alice, due={"date": "2026-09-30", "time": "17:00"})
    alice.post(f"/api/obligations/{ob['id']}/snooze", json={"hours": 2})
    timeline = alice.get(f"/api/obligations/{ob['id']}/timeline").json()
    past = [e["event_type"] for e in timeline if e["kind"] == "past"]
    planned = [e["event_type"] for e in timeline if e["kind"] == "planned"]
    assert past == ["OBLIGATION_CREATED", "SNOOZED"]
    assert planned == ["PLANNED_T-24h", "PLANNED_T-6h", "PLANNED_OVERDUE", "PLANNED_ESCALATED"]
    assert timeline == sorted(timeline, key=lambda e: (e["at"], e["kind"] != "past"))  # chronological, past before planned
    alice.post(f"/api/obligations/{ob['id']}/complete")
    after = alice.get(f"/api/obligations/{ob['id']}/timeline").json()
    assert not [e for e in after if e["kind"] == "planned"]  # a completed obligation plans nothing


def test_invalid_and_unknown_ids(alice):
    assert alice.get("/api/obligations/not-a-uuid").status_code == 422
    assert alice.get(f"/api/obligations/{uuid.uuid4()}").status_code == 404
    assert alice.post(f"/api/obligations/{uuid.uuid4()}/complete").status_code == 404
    assert alice.patch("/api/obligations/123", json={"title": "x"}).status_code == 422


def test_double_click_race_completes_once(alice, db, monkeypatch):
    """Four overlapping 'complete' requests must serialise on the row lock: exactly one changes state.

    The critical section is deliberately widened (sleep inside the transition) and the requests are
    released together by a barrier, so without the lock they genuinely interleave - verified by
    mutation-testing this very test (removing `lock=True` makes it fail)."""
    import concurrent.futures
    import threading
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import lifecycle

    real_transition = lifecycle.transition

    def slow_transition(*args, **kwargs):
        time.sleep(0.3)
        return real_transition(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "transition", slow_transition)
    ob = create(alice)
    cookie = alice.cookies.get("cos_session")
    barrier = threading.Barrier(4)

    def hit(_):
        with TestClient(app, base_url="http://localhost:3000", cookies={"cos_session": cookie}) as c:
            barrier.wait(timeout=10)
            return c.post(f"/api/obligations/{ob['id']}/complete").json()["changed"]

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(hit, range(4)))
    assert sorted(results) == [False, False, False, True]
    assert len(db.scalars(select(AuditEvent).where(AuditEvent.event_type == "COMPLETED")).all()) == 1
