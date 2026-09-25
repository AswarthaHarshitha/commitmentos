"""Notification queue.

Design goals, each enforced here rather than left to convention:

* **No spam** - a notification's identity is (obligation, rung, deadline, channel); the unique
  ``dedupe_key`` makes creating the same reminder twice a no-op, even across concurrent ticks.
* **No lost reminders** - external delivery (email/Telegram) is claim-based. If n8n is down the
  rows simply stay PENDING and are delivered when it returns; a crashed delivery is re-claimed
  after a lease timeout.
* **Bounded retries** - failed deliveries back off and give up after N attempts (visible as FAILED).
* **No reminders after completion** - closing an obligation cancels its queued notifications, and
  ``claim_pending`` re-checks the obligation status as a second line of defence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.enums import (
    TERMINAL_STATUSES,
    AuditEventType,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.models import Notification, Obligation, User
from app.services import audit, messages
from app.services.audit import Actor
from app.services.reminders import ReminderPolicy


def channels_for(policy: ReminderPolicy, *, external: bool = True) -> list[NotificationChannel]:
    channels = [NotificationChannel.IN_APP]
    if external and policy.notify_email:
        channels.append(NotificationChannel.EMAIL)
    if external and policy.notify_telegram:
        channels.append(NotificationChannel.TELEGRAM)
    return channels


def dedupe_key(ob: Obligation | None, rung_key: str, channel: NotificationChannel) -> str:
    epoch = int(ob.due_at.timestamp()) if ob is not None and ob.due_at else 0
    return f"{ob.id if ob is not None else 'none'}:{rung_key}:{epoch}:{channel.value}"


def queue(
    db: Session,
    *,
    user: User,
    ob: Obligation | None,
    kind: NotificationKind,
    rung_key: str,
    content: messages.Content,
    policy: ReminderPolicy,
    now: datetime,
    external: bool = True,
    extra_payload: dict[str, Any] | None = None,
    run_id: uuid.UUID | None = None,
) -> list[Notification]:
    """Create one notification per channel. Returns only the rows that were actually new."""
    created: list[Notification] = []
    for channel in channels_for(policy, external=external):
        in_app = channel == NotificationChannel.IN_APP
        values = {
            "id": uuid.uuid4(),
            "user_id": user.id,
            "obligation_id": ob.id if ob is not None else None,
            "kind": kind,
            "channel": channel,
            "status": NotificationStatus.SENT if in_app else NotificationStatus.PENDING,
            "title": content.title[:300],
            "body": messages.to_text(content),
            "payload": {**content.as_payload(), "rung": rung_key, **(extra_payload or {})},
            "dedupe_key": dedupe_key(ob, rung_key, channel),
            "scheduled_for": now,
            "attempts": 0,
            "sent_at": now if in_app else None,
            "automation_run_id": run_id,
            "created_at": now,
            "updated_at": now,
        }
        new_id = db.execute(
            pg_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_notifications_user_dedupe")
            .returning(Notification.id)
        ).scalar_one_or_none()
        if new_id is None:
            audit.record(
                db,
                AuditEventType.NOTIFICATION_SUPPRESSED,
                f"Skipped duplicate {kind.value.lower().replace('_', ' ')} on {channel.value.lower()}",
                user_id=user.id,
                obligation_id=ob.id if ob is not None else None,
                actor=Actor.system("notifier"),
                data={"rung": rung_key, "channel": channel.value, "reason": "already sent for this deadline"},
                now=now,
            )
            continue
        note = db.get(Notification, new_id)
        assert note is not None
        db.refresh(note)
        created.append(note)
        if in_app and ob is not None:
            ob.last_notified_at = now
    return created


def claim_pending(db: Session, now: datetime, settings: Settings, limit: int = 25) -> list[tuple[Notification, User]]:
    """Atomically hand PENDING external notifications to a deliverer (n8n)."""
    lease_cutoff = now - timedelta(seconds=settings.sending_lease_seconds)
    stmt = (
        select(Notification)
        .where(
            Notification.channel != NotificationChannel.IN_APP,
            Notification.scheduled_for <= now,
            or_(
                and_(
                    Notification.status == NotificationStatus.PENDING,
                    or_(Notification.next_attempt_at.is_(None), Notification.next_attempt_at <= now),
                ),
                and_(Notification.status == NotificationStatus.SENDING, Notification.claimed_at < lease_cutoff),
            ),
        )
        .order_by(Notification.scheduled_for)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed: list[tuple[Notification, User]] = []
    for note in db.scalars(stmt):
        ob = db.get(Obligation, note.obligation_id) if note.obligation_id else None
        if ob is not None and ob.status in TERMINAL_STATUSES and note.kind != NotificationKind.ACTION_RESULT:
            note.status = NotificationStatus.CANCELLED
            note.last_error = "obligation closed before delivery"
            continue
        if note.attempts >= settings.notification_max_attempts:
            _give_up(db, note, "delivery lease expired too many times", now)
            continue
        note.status = NotificationStatus.SENDING
        note.claimed_at = now
        note.attempts += 1
        user = db.get(User, note.user_id)
        assert user is not None
        claimed.append((note, user))
    db.flush()
    return claimed


def delivery_view(note: Notification, user: User, settings: Settings) -> dict[str, Any]:
    content = messages.Content(note.title, note.payload.get("paragraphs", []), note.payload.get("buttons", []))
    return {
        "id": str(note.id),
        "channel": note.channel.value,
        "kind": note.kind.value,
        "attempt": note.attempts,
        "obligation_id": str(note.obligation_id) if note.obligation_id else None,
        "to_email": user.email,
        "from_email": settings.notify_from_email,
        "subject": note.title,
        "text": messages.to_text(content),
        "html": messages.to_html(content),
        "telegram_chat_id": (user.preferences or {}).get("telegram_chat_id"),
    }


def mark_sent(db: Session, note: Notification, now: datetime, run_id: uuid.UUID | None = None, settings: Settings | None = None) -> None:
    if note.status == NotificationStatus.SENT:
        return  # duplicate report from a retried webhook
    late = note.status == NotificationStatus.CANCELLED
    note.status = NotificationStatus.SENT
    note.sent_at = now
    note.last_error = None
    note.next_attempt_at = None
    if run_id:
        note.automation_run_id = run_id
    if note.obligation_id:
        ob = db.get(Obligation, note.obligation_id)
        if ob is not None:
            ob.last_notified_at = now
    verb = "delivered" if note.channel == NotificationChannel.IN_APP else "sent"
    local_mail = note.channel == NotificationChannel.EMAIL and (settings or get_settings()).mail_goes_to_local_sink
    audit.record(
        db,
        AuditEventType.NOTIFICATION_SENT,
        (
            f"{messages.kind_phrase(note.kind)} placed {messages.LOCAL_INBOX_NOTE} (email delivery is not set up)"
            if local_mail
            else f"{messages.kind_phrase(note.kind)} {verb} {messages.channels_phrase([note.channel.value])}"
        )
        + (" (delivered just after the commitment was closed)" if late else ""),
        user_id=note.user_id,
        obligation_id=note.obligation_id,
        actor=Actor.n8n(),
        data={"notification_id": note.id, "channel": note.channel, "kind": note.kind, "attempt": note.attempts},
        run_id=run_id,
        now=now,
    )


def mark_failed(db: Session, note: Notification, error: str, now: datetime, settings: Settings, run_id: uuid.UUID | None = None) -> None:
    # A failure report must never resurrect a notification that was cancelled (obligation closed
    # while it was in flight) or already reached a final state.
    if note.status in (NotificationStatus.SENT, NotificationStatus.FAILED, NotificationStatus.CANCELLED):
        return
    error = error[:500]
    if note.attempts >= settings.notification_max_attempts:
        _give_up(db, note, error, now, run_id)
        return
    schedule = settings.notification_retry_backoff_seconds
    delay = schedule[min(note.attempts - 1, len(schedule) - 1)] if schedule else 60
    note.status = NotificationStatus.PENDING
    note.last_error = error
    note.next_attempt_at = now + timedelta(seconds=delay)
    audit.record(
        db,
        AuditEventType.NOTIFICATION_FAILED,
        f"Delivery via {note.channel.value.lower()} failed (attempt {note.attempts}); will retry in {delay}s",
        user_id=note.user_id,
        obligation_id=note.obligation_id,
        actor=Actor.n8n(),
        data={"notification_id": note.id, "error": error, "attempt": note.attempts, "retry_in_seconds": delay},
        run_id=run_id,
        now=now,
    )


def _give_up(db: Session, note: Notification, error: str, now: datetime, run_id: uuid.UUID | None = None) -> None:
    note.status = NotificationStatus.FAILED
    note.last_error = error[:500]
    note.next_attempt_at = None
    audit.record(
        db,
        AuditEventType.NOTIFICATION_FAILED,
        f"Delivery via {note.channel.value.lower()} failed permanently after {note.attempts} attempt(s)",
        user_id=note.user_id,
        obligation_id=note.obligation_id,
        actor=Actor.n8n(),
        data={"notification_id": note.id, "error": error[:500], "attempts": note.attempts, "final": True},
        run_id=run_id,
        now=now,
    )
