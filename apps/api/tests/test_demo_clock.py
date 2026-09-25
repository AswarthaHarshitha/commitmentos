"""The demo clock: time travel that exists only in demo mode and that every deadline rule obeys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import get_settings
from app.enums import NotificationKind
from app.models import Notification, SystemSetting
from app.services import demo
from tests.factories import make_obligation, make_user

URL = "/api/internal/demo/clock"


@pytest.fixture
def demo_on(app):
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"demo_mode": True})
    yield
    clock.reset()


def test_time_travel_does_not_exist_outside_demo_mode(client, n8n_headers):
    assert client.get(URL, headers=n8n_headers).status_code == 404
    assert client.post(URL, headers=n8n_headers, json={"advance_seconds": 3600}).status_code == 404
    assert clock.offset == timedelta(0)


def test_it_still_requires_the_service_secret(client, demo_on):
    assert client.get(URL).status_code == 401
    assert client.post(URL, json={"reset": True}, headers={"X-Webhook-Secret": "wrong"}).status_code == 401


def test_advance_moves_the_clock_and_is_persisted(client, n8n_headers, demo_on, db):
    before = client.get(URL, headers=n8n_headers).json()
    assert before["offset_seconds"] == 0
    after = client.post(URL, headers=n8n_headers, json={"advance_seconds": 3 * 3600}).json()
    assert after["offset_seconds"] == 3 * 3600
    assert datetime.fromisoformat(after["now"]) - datetime.fromisoformat(after["real_now"]) == pytest.approx(timedelta(hours=3), abs=timedelta(seconds=2))
    assert db.get(SystemSetting, "demo_clock").value == {"offset_seconds": 3 * 3600}
    again = client.post(URL, headers=n8n_headers, json={"advance_seconds": 3600}).json()
    assert again["offset_seconds"] == 4 * 3600  # advances accumulate


def test_a_restart_restores_the_persisted_offset(client, n8n_headers, demo_on, db):
    client.post(URL, headers=n8n_headers, json={"advance_seconds": 86_400})
    clock.reset()  # what a fresh process looks like
    assert clock.offset == timedelta(0)
    demo.restore_clock(db)
    assert clock.offset == timedelta(days=1)


def test_set_to_jumps_to_a_moment_and_reset_returns_to_real_time(client, n8n_headers, demo_on):
    target = datetime.now(UTC) + timedelta(days=2, hours=5)
    got = client.post(URL, headers=n8n_headers, json={"set_to": target.isoformat()}).json()
    assert got["offset_seconds"] == pytest.approx(2 * 86_400 + 5 * 3600, abs=5)
    assert client.post(URL, headers=n8n_headers, json={"reset": True}).json()["offset_seconds"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {},  # nothing to do
        {"advance_seconds": 60, "reset": True},  # ambiguous
        {"advance_seconds": 0},
        {"advance_seconds": -5},
        {"advance_seconds": 91 * 86_400},  # beyond the 90-day guard rail
        {"set_to": "2026-09-24T12:00:00"},  # no UTC offset: which moment is that?
        {"advance_seconds": 60, "surprise": 1},
    ],
)
def test_invalid_commands_are_rejected_and_change_nothing(client, n8n_headers, demo_on, body):
    assert client.post(URL, headers=n8n_headers, json=body).status_code == 422
    assert clock.offset == timedelta(0)


def test_advancing_time_drives_the_real_reminder_ladder(client, n8n_headers, demo_on, db):
    """The point of the feature: fast-forward and the deterministic monitor does what it would have done in real time."""
    user = make_user(db, "demo@example.com")
    ob = make_obligation(db, user, due_at=clock.now() + timedelta(hours=30))
    ob.next_action_at = ob.due_at - timedelta(hours=24)
    db.commit()

    def reminders() -> list[Notification]:
        db.expire_all()
        return db.scalars(select(Notification).where(Notification.kind == NotificationKind.REMINDER)).all()

    client.post("/api/internal/monitor/tick", headers=n8n_headers)
    assert reminders() == []  # 30h out: nothing is due yet

    client.post(URL, headers=n8n_headers, json={"advance_seconds": 7 * 3600})  # now 23h before the deadline
    client.post("/api/internal/monitor/tick", headers=n8n_headers)
    assert len(reminders()) >= 1
