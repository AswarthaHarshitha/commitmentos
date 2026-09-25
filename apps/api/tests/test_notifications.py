"""Notification queue semantics: claim, lease, retry/backoff, give-up, idempotent reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.enums import AuditEventType
from app.enums import NotificationChannel as Ch
from app.enums import NotificationKind as K
from app.enums import NotificationStatus as NS
from app.models import AuditEvent
from app.services import messages, notifications
from app.services.reminders import policy_for
from tests.factories import make_obligation, make_user

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _pending_email(db, key="k"):
    user = make_user(db)
    ob = make_obligation(db, user, due_at=NOW + timedelta(hours=5))
    policy = policy_for(user, settings)
    content = messages.build(K.REMINDER, ob, policy.tz, NOW, settings)
    created = notifications.queue(db, user=user, ob=ob, kind=K.REMINDER, rung_key=key, content=content, policy=policy, now=NOW)
    db.commit()
    return user, ob, next(n for n in created if n.channel == Ch.EMAIL)


def test_claim_hands_out_pending_email_once_and_marks_it_sending(db):
    user, ob, note = _pending_email(db)
    claimed = notifications.claim_pending(db, NOW, settings)
    db.commit()
    assert [n.id for n, _ in claimed] == [note.id]
    db.refresh(note)
    assert note.status == NS.SENDING and note.attempts == 1 and note.claimed_at == NOW
    assert notifications.claim_pending(db, NOW, settings) == []  # exclusive: nobody else gets it


def test_in_app_notifications_are_never_handed_to_the_deliverer(db):
    _pending_email(db)
    claimed = notifications.claim_pending(db, NOW, settings)
    assert all(n.channel != Ch.IN_APP for n, _ in claimed)


def test_delivery_view_contains_what_n8n_needs_and_escapes_html(db):
    user, ob, note = _pending_email(db)
    ob.title = "<script>alert(1)</script> Submit form"
    note.payload = {**note.payload, "paragraphs": ["<b>bold</b> & more"]}
    view = notifications.delivery_view(note, user, settings)
    assert view["to_email"] == user.email and view["channel"] == "EMAIL" and view["subject"]
    assert "<b>bold</b>" not in view["html"] and "&lt;b&gt;bold&lt;/b&gt;" in view["html"]
    assert "Open in CommitmentOS" in view["text"]


def test_a_crashed_delivery_is_reclaimed_after_the_lease_expires(db):
    _, _, note = _pending_email(db)
    notifications.claim_pending(db, NOW, settings)
    db.commit()
    assert notifications.claim_pending(db, NOW + timedelta(seconds=60), settings) == []  # lease still valid
    again = notifications.claim_pending(db, NOW + timedelta(seconds=settings.sending_lease_seconds + 1), settings)
    db.commit()
    assert len(again) == 1
    db.refresh(note)
    assert note.attempts == 2


def test_failure_backs_off_then_retries_then_gives_up_and_says_so(db):
    _, ob, note = _pending_email(db)
    t = NOW
    for attempt in range(1, settings.notification_max_attempts + 1):
        claimed = notifications.claim_pending(db, t, settings)
        assert len(claimed) == 1, f"attempt {attempt} should be claimable"
        notifications.mark_failed(db, note, "SMTP 421 try later", t, settings)
        db.commit()
        db.refresh(note)
        if attempt < settings.notification_max_attempts:
            assert note.status == NS.PENDING and note.next_attempt_at > t
            assert notifications.claim_pending(db, t, settings) == []  # respects the backoff
            t = note.next_attempt_at
    assert note.status == NS.FAILED and note.attempts == settings.notification_max_attempts
    assert notifications.claim_pending(db, t + timedelta(days=1), settings) == []  # never retried again
    final = db.scalars(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_FAILED)).all()
    assert final[-1].data["final"] is True


def test_backoff_delays_grow_per_the_configured_schedule(db):
    _, _, note = _pending_email(db)
    delays = []
    t = NOW
    for _ in range(settings.notification_max_attempts - 1):
        notifications.claim_pending(db, t, settings)
        notifications.mark_failed(db, note, "x", t, settings)
        delays.append(int((note.next_attempt_at - t).total_seconds()))
        t = note.next_attempt_at
    assert delays == settings.notification_retry_backoff_seconds[: len(delays)]


def test_reporting_sent_twice_is_idempotent(db):
    _, _, note = _pending_email(db)
    notifications.claim_pending(db, NOW, settings)
    notifications.mark_sent(db, note, NOW)
    notifications.mark_sent(db, note, NOW + timedelta(minutes=1))
    db.commit()
    sent_events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_SENT)).all()
    assert len(sent_events) == 1 and note.sent_at == NOW


def test_a_failure_report_cannot_resurrect_a_cancelled_notification(db):
    _, _, note = _pending_email(db)
    notifications.claim_pending(db, NOW, settings)
    note.status = NS.CANCELLED  # obligation was completed while n8n was mid-delivery
    notifications.mark_failed(db, note, "timeout", NOW, settings)
    assert note.status == NS.CANCELLED


def test_a_delivery_that_finishes_after_cancellation_is_recorded_honestly(db):
    _, _, note = _pending_email(db)
    notifications.claim_pending(db, NOW, settings)
    note.status = NS.CANCELLED
    notifications.mark_sent(db, note, NOW)
    db.commit()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_SENT))
    assert "just after the obligation was closed" in ev.message


def test_lease_expiring_too_many_times_gives_up_instead_of_looping_forever(db):
    _, _, note = _pending_email(db)
    t = NOW
    for _ in range(settings.notification_max_attempts):
        notifications.claim_pending(db, t, settings)  # claimed, then n8n dies: no report ever arrives
        t += timedelta(seconds=settings.sending_lease_seconds + 1)
    assert notifications.claim_pending(db, t, settings) == []
    db.refresh(note)
    assert note.status == NS.FAILED


@pytest.mark.parametrize("action,expected", [("Submit signed internship documents", "Submit signed internship documents"), ("submit", "Submit the form"), (None, "Submit the form"), ("  ", "Submit the form")])
def test_the_detection_message_describes_the_action_but_falls_back_to_the_title_when_the_action_is_a_single_word(db, action, expected):
    user = make_user(db, "n@example.com")
    ob = make_obligation(db, user, title="Submit the form", action=action, due_at=NOW + timedelta(days=2))
    policy = policy_for(user, settings)
    content = messages.build(K.DETECTED, ob, policy.tz, NOW, settings)
    assert content.paragraphs[0].startswith("Found in") and content.paragraphs[0].endswith(f": {expected}.")
