"""Dashboard logic: what needs attention *now*, computed in the user's own timezone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.enums import ObligationStatus as S
from app.enums import ObligationType as T
from app.enums import Priority as P
from app.models import User
from tests.factories import make_obligation

# 2026-09-24 12:00 UTC = 08:00 Thu in New York
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


def _alice(db):
    return db.scalar(select(User).where(User.email == "alice@example.com"))


def dash(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_account_is_calm_and_says_so(alice):
    d = dash(alice)
    assert d["summary"]["tone"] == "calm" and d["summary"]["headline"] == "Your commitments are under control."
    assert d["summary"]["subline"] == "Nothing is due today."
    assert d["focus"] is None and d["upcoming"] == [] and d["part_of_day"] == "morning"
    assert d["timezone"] == "America/New_York" and d["display_name"] == "Alice Example"


def test_calm_state_still_points_at_whats_next(alice, db):
    make_obligation(db, _alice(db), title="Renew passport", status=S.OPEN, due_at=NOW + timedelta(days=4), acknowledged_at=NOW)
    db.commit()
    assert "Next up: Renew passport" in dash(alice)["summary"]["subline"]


def test_due_today_uses_the_users_local_day_not_the_utc_day(alice, db):
    # 03:30 UTC on Sep 25 is 23:30 EDT on Sep 24 -> still "today" for Alice, though it is tomorrow in UTC
    make_obligation(db, _alice(db), title="late tonight", status=S.OPEN, due_at=datetime(2026, 9, 25, 3, 30, tzinfo=UTC), acknowledged_at=NOW)
    # 04:30 UTC on Sep 25 is 00:30 EDT on Sep 25 -> tomorrow
    make_obligation(db, _alice(db), title="just after midnight", status=S.OPEN, due_at=datetime(2026, 9, 25, 4, 30, tzinfo=UTC), acknowledged_at=NOW)
    db.commit()
    d = dash(alice)
    assert [o["title"] for o in d["today"]["due_today"]] == ["late tonight"]
    assert [g["label"] for g in d["upcoming"]] == ["Tomorrow"] and d["upcoming"][0]["items"][0]["title"] == "just after midnight"
    assert d["summary"]["tone"] == "attention" and d["summary"]["headline"] == "1 commitment needs your attention today."


def test_overdue_dominates_the_headline_and_the_focus(alice, db):
    u = _alice(db)
    make_obligation(db, u, title="soon", status=S.ACTION_REQUIRED, due_at=NOW + timedelta(hours=3), priority=P.URGENT, acknowledged_at=NOW)
    make_obligation(db, u, title="late", status=S.OVERDUE, due_at=NOW - timedelta(days=2), acknowledged_at=NOW)
    make_obligation(db, u, title="way late", status=S.ESCALATED, due_at=NOW - timedelta(days=5), acknowledged_at=NOW)
    db.commit()
    d = dash(alice)
    assert d["summary"]["tone"] == "critical" and d["summary"]["headline"].startswith("2 overdue and 1 due today")
    assert d["focus"]["title"] == "way late"  # escalated outranks overdue outranks merely-soon
    assert [o["title"] for o in d["today"]["overdue"]] == ["way late", "late"]
    assert d["summary"]["attention_count"] == 3


def test_focus_prefers_the_earlier_deadline_then_priority_among_equals(alice, db):
    u = _alice(db)
    make_obligation(db, u, title="in 5h", status=S.ACTION_REQUIRED, due_at=NOW + timedelta(hours=5), priority=P.LOW, acknowledged_at=NOW)
    make_obligation(db, u, title="in 2h", status=S.ACTION_REQUIRED, due_at=NOW + timedelta(hours=2), priority=P.LOW, acknowledged_at=NOW)
    db.commit()
    assert dash(alice)["focus"]["title"] == "in 2h"


def test_nothing_urgent_means_no_focus_item(alice, db):
    make_obligation(db, _alice(db), title="far", status=S.OPEN, due_at=NOW + timedelta(days=9), acknowledged_at=NOW)
    db.commit()
    assert dash(alice)["focus"] is None


def test_upcoming_is_grouped_by_local_day_within_a_week(alice, db):
    u = _alice(db)
    for title, days in [("d2a", 2), ("d2b", 2), ("d5", 5), ("d20", 20)]:
        make_obligation(db, u, title=title, status=S.OPEN, due_at=NOW + timedelta(days=days), acknowledged_at=NOW)
    db.commit()
    groups = dash(alice)["upcoming"]
    assert [g["label"] for g in groups] == ["Saturday, Sep 26", "Tuesday, Sep 29"]
    assert [len(g["items"]) for g in groups] == [2, 1]  # 20 days out is beyond the 7-day horizon


def test_appointments_are_surfaced_separately_and_only_when_tracked(alice, db):
    u = _alice(db)
    make_obligation(db, u, title="Interview", obligation_type=T.INTERVIEW, status=S.OPEN, due_at=NOW + timedelta(hours=30), acknowledged_at=NOW)
    make_obligation(db, u, title="Not tracked", obligation_type=T.APPOINTMENT, status=S.NEEDS_REVIEW, due_at=NOW + timedelta(hours=30))
    db.commit()
    assert [o["title"] for o in dash(alice)["today"]["appointments"]] == ["Interview"]


def test_recent_detections_are_only_unacknowledged_open_items(alice, db):
    u = _alice(db)
    make_obligation(db, u, title="new one", status=S.NEEDS_REVIEW, due_at=NOW + timedelta(days=3))
    make_obligation(db, u, title="seen", status=S.OPEN, due_at=NOW + timedelta(days=3), acknowledged_at=NOW)
    make_obligation(db, u, title="finished", status=S.COMPLETED)
    db.commit()
    d = dash(alice)
    assert [o["title"] for o in d["recent_detections"]] == ["new one"] and d["summary"]["inbox_unreviewed"] == 1


def test_status_counts_and_part_of_day_follow_the_app_clock(alice, db):
    u = _alice(db)
    make_obligation(db, u, status=S.OPEN, acknowledged_at=NOW)
    make_obligation(db, u, status=S.OPEN, acknowledged_at=NOW)
    make_obligation(db, u, status=S.COMPLETED)
    db.commit()
    clock.freeze(datetime(2026, 9, 24, 22, 30, tzinfo=UTC))  # 18:30 in New York
    d = dash(alice)
    assert d["status_counts"] == {"OPEN": 2, "COMPLETED": 1} and d["part_of_day"] == "evening"


def test_system_status_exposes_the_clock_so_the_ui_can_agree_with_the_server(alice):
    s = alice.get("/api/system/status").json()
    assert s["now"] == "2026-09-24T12:00:00Z" and s["demo_mode"] is False and s["llm_provider"] == "none" and s["calendar_provider"] == "local"
    assert s["n8n"]["checked"] in (True, False) and s["version"]


def test_system_status_reports_the_llm_that_will_really_be_used_for_any_provider(alice, app):
    """Regression: 'configured' used to be computed per provider name and was False for Gemini even with a key set."""
    from app.services.extraction.llm import GeminiClient, get_llm_client

    assert alice.get("/api/system/status").json()["llm_configured"] is False  # LLM_PROVIDER=none in the test environment
    app.dependency_overrides[get_llm_client] = lambda: GeminiClient(api_key="k" * 20, model="gemini-3-flash-preview", thinking_budget=0, timeout=5, max_retries=0)
    s = alice.get("/api/system/status").json()
    assert s["llm_configured"] is True and s["llm_model"] == "gemini-3-flash-preview"
    assert "k" * 20 not in str(s)
