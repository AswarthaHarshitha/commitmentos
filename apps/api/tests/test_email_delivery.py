"""The product says truthfully where email goes: into the bundled local test inbox, or out through a real SMTP account."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.clock import clock
from app.config import Settings, get_settings
from app.enums import ApprovalAction, AuditEventType, NotificationChannel, NotificationKind, NotificationStatus
from app.models import AuditEvent, Notification
from app.services import approvals, notifications
from tests.factories import make_obligation, make_user

NOW = datetime(2026, 9, 23, 15, 5, tzinfo=UTC)
LOCAL = get_settings().model_copy(update={"smtp_host": "mailpit"})
REAL = get_settings().model_copy(update={"smtp_host": "smtp.example.net"})


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(NOW)


@pytest.mark.parametrize(
    ("host", "local"),
    [("mailpit", True), ("MailPit", True), ("localhost", True), ("127.0.0.1", True), ("::1", True), ("", True), ("  ", True),
     ("smtp.gmail.com", False), ("mail.example.org", False), ("mailpit.example.org", False)],
)
def test_only_a_local_server_counts_as_the_local_test_inbox(host, local):
    assert Settings(smtp_host=host).mail_goes_to_local_sink is local


@pytest.mark.parametrize(("settings", "expected"), [(LOCAL, "local_test_inbox"), (REAL, "smtp")])
def test_the_system_status_tells_the_interface_where_email_goes(alice, app, settings, expected):
    app.dependency_overrides[get_settings] = lambda: settings
    assert alice.get("/api/system/status").json()["email_delivery"] == expected


def _executed_follow_up(db, settings):
    user = make_user(db, "alice@example.com")
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1), counterparty_email="dana@example.org")
    a, _ = approvals.propose(
        db, user=user, ob=ob, action=ApprovalAction.SEND_FOLLOW_UP, title="Send follow-up to Dana", proposed_by="SYSTEM", rationale=None, now=NOW,
        settings=settings, payload={"to": "dana@example.org", "subject": "s", "body": "b"},
    )
    approvals.approve(db, user, a, NOW, settings)
    approvals.claim(db, NOW, settings)
    approvals.report_result(db, a, ok=True, result={"accepted": ["dana@example.org"]}, error=None, now=NOW, settings=settings)
    db.commit()
    return db.scalar(select(AuditEvent.message).where(AuditEvent.event_type == AuditEventType.ACTION_EXECUTED))


def test_a_follow_up_into_the_local_inbox_is_not_recorded_as_sent(db):
    message = _executed_follow_up(db, LOCAL)
    assert "local test inbox" in message and "not to a real mailbox" in message and not message.startswith("Sent")


def test_a_follow_up_through_a_real_server_is_recorded_as_sent(db):
    assert _executed_follow_up(db, REAL) == "Sent: Send follow-up to Dana"


@pytest.mark.parametrize(("settings", "phrase"), [(LOCAL, "local test inbox"), (REAL, "It went through.")])
def test_the_result_notice_matches_where_the_email_went(db, settings, phrase):
    _executed_follow_up(db, settings)
    body = db.scalar(select(Notification.body).where(Notification.kind == NotificationKind.ACTION_RESULT, Notification.channel == NotificationChannel.IN_APP))
    assert phrase in body
    assert ("Nothing reached a real mailbox" in body) is (settings is LOCAL)


def _sent_notification(db, settings, channel):
    user = make_user(db, "alice@example.com")
    ob = make_obligation(db, user, due_at=NOW + timedelta(days=1))
    note = Notification(
        user_id=user.id, obligation_id=ob.id, kind=NotificationKind.REMINDER, channel=channel, status=NotificationStatus.SENDING,
        title="Reminder", body="b", dedupe_key=f"k-{channel.value}", scheduled_for=NOW,
    )
    db.add(note)
    db.flush()
    notifications.mark_sent(db, note, NOW, settings=settings)
    db.commit()
    return db.scalars(select(AuditEvent.message).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_SENT)).all()[-1]


def test_a_reminder_email_into_the_local_inbox_says_so(db):
    message = _sent_notification(db, LOCAL, NotificationChannel.EMAIL)
    assert message.startswith("Reminder placed into the local test inbox") and "email delivery is not set up" in message


def test_a_reminder_email_through_a_real_server_is_simply_sent(db):
    assert _sent_notification(db, REAL, NotificationChannel.EMAIL) == "Reminder sent by email"


def test_an_in_app_notification_is_unaffected_by_where_email_goes(db):
    assert _sent_notification(db, LOCAL, NotificationChannel.IN_APP) == "Reminder delivered in the app"
