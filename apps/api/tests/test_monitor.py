"""Deadline monitor: reminders, overdue, escalation, anti-spam, catch-up, snooze, completion, concurrency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.clock import clock
from app.config import get_settings
from app.enums import (
    ApprovalAction,
    ApprovalStatus,
    AuditEventType,
    DuePrecision,
)
from app.enums import (
    NotificationChannel as Ch,
)
from app.enums import (
    NotificationKind as K,
)
from app.enums import (
    NotificationStatus as NS,
)
from app.enums import (
    ObligationStatus as S,
)
from app.models import ApprovalRequest, AuditEvent, Notification
from app.services import lifecycle, monitor, notifications
from app.services.audit import Actor
from app.services.reminders import build_ladder, policy_for
from app.services.timeutil import end_of_local_day, local_to_utc
from tests.factories import make_obligation, make_user

settings = get_settings()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
DUE = NOW + timedelta(days=3)  # 2026-09-27 12:00Z


def _setup(db, *, due=DUE, status=S.OPEN, user=None, **kw):
    user = user or make_user(db)
    ob = make_obligation(db, user, status=status, due_at=due, **kw)
    monitor.refresh_schedule(ob, user, NOW, settings)
    db.commit()
    return user, ob


def _tick(db, at: datetime):
    clock.freeze(at)
    summary = monitor.run_tick(db, at, settings)
    db.commit()
    return summary


def _notes(db, ob=None):
    stmt = select(Notification).order_by(Notification.created_at, Notification.channel)
    if ob is not None:
        stmt = stmt.where(Notification.obligation_id == ob.id)
    return list(db.scalars(stmt))


def test_nothing_happens_before_the_first_reminder_rung(db):
    _, ob = _setup(db)
    assert ob.next_action_at == DUE - timedelta(hours=24)
    s = _tick(db, NOW + timedelta(hours=1))
    assert s.evaluated == 0 and _notes(db) == []


def test_24h_reminder_is_queued_once_per_channel_and_moves_state_to_action_required(db):
    _, ob = _setup(db)
    s = _tick(db, DUE - timedelta(hours=24))
    assert s.reminders_queued == 1
    notes = _notes(db, ob)
    assert {(n.kind, n.channel) for n in notes} == {(K.REMINDER, Ch.IN_APP), (K.REMINDER, Ch.EMAIL)}
    in_app = next(n for n in notes if n.channel == Ch.IN_APP)
    email = next(n for n in notes if n.channel == Ch.EMAIL)
    assert in_app.status == NS.SENT and email.status == NS.PENDING  # in-app is immediate, email awaits n8n
    db.refresh(ob)
    assert ob.status == S.ACTION_REQUIRED
    assert ob.next_action_at == DUE - timedelta(hours=6)


def test_6h_is_a_high_priority_reminder_then_overdue_then_escalated(db):
    _, ob = _setup(db)
    _tick(db, DUE - timedelta(hours=24))
    _tick(db, DUE - timedelta(hours=6))
    _tick(db, DUE + timedelta(minutes=1))
    _tick(db, DUE + timedelta(hours=24, minutes=1))
    db.refresh(ob)
    assert ob.status == S.ESCALATED
    kinds = [n.kind for n in _notes(db, ob) if n.channel == Ch.EMAIL]
    assert kinds == [K.REMINDER, K.HIGH_PRIORITY_REMINDER, K.OVERDUE, K.ESCALATION]
    assert ob.next_action_at is None  # nothing left to do: the ladder is exhausted


def test_repeated_ticks_at_the_same_time_do_not_create_more_notifications(db):
    _, ob = _setup(db)
    at = DUE - timedelta(hours=24)
    _tick(db, at)
    before = len(_notes(db))
    for _ in range(3):
        s = _tick(db, at)
        assert s.reminders_queued == 0
    _tick(db, at + timedelta(minutes=5))
    assert len(_notes(db)) == before


def test_duplicate_reminder_creation_is_suppressed_and_audited_not_silent(db):
    user, ob = _setup(db)
    policy = policy_for(user, settings)
    from app.services import messages

    content = messages.build(K.REMINDER, ob, policy.tz, NOW, settings)
    first = notifications.queue(db, user=user, ob=ob, kind=K.REMINDER, rung_key="T-24h", content=content, policy=policy, now=NOW)
    second = notifications.queue(db, user=user, ob=ob, kind=K.REMINDER, rung_key="T-24h", content=content, policy=policy, now=NOW)
    db.commit()
    assert len(first) == 2 and second == []
    suppressed = db.scalars(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_SUPPRESSED)).all()
    assert len(suppressed) == 2  # one per channel


def test_overdue_detection_marks_status_and_notifies(db):
    _, ob = _setup(db, status=S.ACTION_REQUIRED, due=NOW + timedelta(hours=2))
    s = _tick(db, NOW + timedelta(hours=2, minutes=1))
    db.refresh(ob)
    assert ob.status == S.OVERDUE and s.marked_overdue == 1
    assert K.OVERDUE in {n.kind for n in _notes(db, ob)}


def test_escalation_uses_the_configured_period(db):
    from app.config import Settings

    custom = Settings(_env_file=None, escalate_after_hours=2, jwt_secret="x" * 40, n8n_inbound_secret="y" * 30, n8n_outbound_secret="z" * 30)
    user = make_user(db)
    ob = make_obligation(db, user, status=S.OPEN, due_at=NOW + timedelta(hours=1))
    monitor.refresh_schedule(ob, user, NOW, custom)
    db.commit()
    clock.freeze(NOW + timedelta(hours=1, minutes=1))
    monitor.run_tick(db, clock.now(), custom)
    db.refresh(ob)
    assert ob.status == S.OVERDUE
    clock.freeze(NOW + timedelta(hours=3, minutes=1))
    monitor.run_tick(db, clock.now(), custom)
    db.refresh(ob)
    assert ob.status == S.ESCALATED


def test_after_downtime_only_the_most_severe_rung_is_sent_not_a_burst(db):
    """System offline for days: the user gets ONE 'escalated' message, not reminder+high+overdue+escalated."""
    _, ob = _setup(db)
    s = _tick(db, DUE + timedelta(days=3))
    db.refresh(ob)
    assert ob.status == S.ESCALATED
    email = [n for n in _notes(db, ob) if n.channel == Ch.EMAIL]
    assert [n.kind for n in email] == [K.ESCALATION]
    assert s.reminders_queued == 1 and s.marked_overdue == 1 and s.escalated == 1
    queued = db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.NOTIFICATION_QUEUED))
    assert queued.data["superseded_rungs"] == ["T-24h", "T-6h", "OVERDUE"]
    statuses = [e.data["to"] for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type.in_([AuditEventType.OVERDUE_MARKED, AuditEventType.ESCALATED])).order_by(AuditEvent.id))]
    assert statuses == ["OVERDUE", "ESCALATED"]  # the state history is still complete


def test_created_inside_the_window_does_not_fire_reminders_that_are_already_in_the_past(db):
    _, ob = _setup(db, due=NOW + timedelta(hours=3))  # T-24h and T-6h are both already past
    assert ob.next_action_at == ob.due_at  # first real action is the overdue check
    s = _tick(db, NOW + timedelta(minutes=30))
    assert s.evaluated == 0 and _notes(db) == []


def test_snooze_suppresses_notifications_but_never_hides_that_a_deadline_passed(db):
    _, ob = _setup(db, due=NOW + timedelta(hours=2))
    ob.snoozed_until = NOW + timedelta(hours=5)
    monitor.refresh_schedule(ob, db.get(type(make_user(db)), ob.user_id), NOW, settings)
    db.commit()
    s = _tick(db, NOW + timedelta(hours=2, minutes=1))
    db.refresh(ob)
    assert ob.status == S.OVERDUE  # fact recorded
    assert s.reminders_queued == 0 and _notes(db, ob) == []  # but no ping while snoozed
    _tick(db, NOW + timedelta(hours=5, minutes=1))  # snooze over
    assert K.OVERDUE in {n.kind for n in _notes(db, ob)}


def test_completed_obligation_is_never_notified_again(db):
    user, ob = _setup(db)
    _tick(db, DUE - timedelta(hours=24))
    lifecycle.transition(db, ob, S.COMPLETED, actor=Actor.user(user), now=DUE - timedelta(hours=23), via="API")
    db.commit()
    before = len(_notes(db, ob))
    for offset in (timedelta(hours=-6), timedelta(minutes=1), timedelta(hours=25)):
        s = _tick(db, DUE + offset)
        assert s.evaluated == 0
    assert len(_notes(db, ob)) == before


def test_pending_email_is_cancelled_on_completion_and_never_handed_to_n8n(db):
    user, ob = _setup(db)
    _tick(db, DUE - timedelta(hours=24))
    lifecycle.transition(db, ob, S.COMPLETED, actor=Actor.user(user), now=DUE - timedelta(hours=23), via="EMAIL_LINK")
    db.commit()
    clock.freeze(DUE - timedelta(hours=22))
    claimed = notifications.claim_pending(db, clock.now(), settings)
    db.commit()
    assert claimed == []
    email = next(n for n in _notes(db, ob) if n.channel == Ch.EMAIL)
    assert email.status == NS.CANCELLED


def test_claim_is_a_second_line_of_defence_against_reminders_for_closed_obligations(db):
    """Even if a PENDING row somehow survived (e.g. a race), claim() refuses to deliver it."""
    user, ob = _setup(db)
    _tick(db, DUE - timedelta(hours=24))
    ob.status = S.COMPLETED  # simulate a path that bypassed the lifecycle service
    ob.completed_at = NOW
    db.commit()
    claimed = notifications.claim_pending(db, DUE - timedelta(hours=23), settings)
    db.commit()
    assert claimed == []
    assert next(n for n in _notes(db, ob) if n.channel == Ch.EMAIL).status == NS.CANCELLED


def test_reopening_an_overdue_item_is_picked_up_immediately(db):
    user, ob = _setup(db, status=S.OPEN, due=NOW - timedelta(hours=1))
    lifecycle.transition(db, ob, S.COMPLETED, actor=Actor.user(user), now=NOW, via="API")
    lifecycle.transition(db, ob, S.OPEN, actor=Actor.user(user), now=NOW)
    monitor.refresh_schedule(ob, user, NOW, settings)
    assert ob.next_action_at == NOW  # already past due -> monitor must act on the next tick
    db.commit()
    _tick(db, NOW + timedelta(minutes=1))
    db.refresh(ob)
    assert ob.status == S.OVERDUE


def test_date_only_deadlines_anchor_reminders_to_business_day_end_not_midnight(db):
    ny = ZoneInfo("America/New_York")
    user = make_user(db, timezone="America/New_York")
    due = end_of_local_day(datetime(2026, 9, 25).date(), ny)  # Fri 23:59:59 EDT
    policy = policy_for(user, settings)
    ladder = {r.key: r.at for r in build_ladder(due, DuePrecision.DATE, policy)}
    assert ladder["T-24h"] == local_to_utc(datetime(2026, 9, 24, 17, 0), ny)  # Thursday 17:00 local
    assert ladder["T-6h"] == local_to_utc(datetime(2026, 9, 25, 11, 0), ny)  # Friday 11:00 local
    assert ladder["OVERDUE"] == due  # but overdue only once the day has really ended


def test_reminder_offsets_are_elapsed_time_even_across_a_dst_change(db):
    ny = ZoneInfo("America/New_York")
    user = make_user(db, timezone="America/New_York")
    due = local_to_utc(datetime(2026, 3, 8, 9, 0), ny)  # 09:00 EDT, the morning clocks changed
    ladder = {r.key: r.at for r in build_ladder(due, DuePrecision.DATETIME, policy_for(user, settings))}
    assert due - ladder["T-24h"] == timedelta(hours=24)
    assert due - ladder["T-6h"] == timedelta(hours=6)


def test_unreviewed_item_with_a_close_deadline_gets_exactly_one_nudge(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.NEEDS_REVIEW, due_at=NOW + timedelta(hours=10))
    db.commit()
    assert _tick(db, NOW).review_nudges == 1
    assert _tick(db, NOW + timedelta(hours=1)).review_nudges == 0
    assert {n.kind for n in _notes(db, ob)} == {K.NEEDS_REVIEW}


def test_review_items_are_not_treated_as_active_obligations(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.NEEDS_REVIEW, due_at=NOW - timedelta(days=1))
    db.commit()
    _tick(db, NOW)
    db.refresh(ob)
    assert ob.status == S.NEEDS_REVIEW  # never auto-marked overdue: the user has not accepted it yet


def test_obligation_stuck_in_detected_is_swept_to_needs_review(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.DETECTED)
    ob.created_at = NOW - timedelta(minutes=30)
    db.commit()
    assert _tick(db, NOW).stale_detected_swept == 1
    db.refresh(ob)
    assert ob.status == S.NEEDS_REVIEW


def test_pending_approvals_expire_and_the_expiry_is_audited(db):
    user = make_user(db)
    ob = make_obligation(db, user)
    a = ApprovalRequest(
        user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.SEND_FOLLOW_UP, title="Send follow-up",
        payload={}, expires_at=NOW - timedelta(minutes=1),
    )
    db.add(a)
    db.commit()
    assert _tick(db, NOW).approvals_expired == 1
    db.refresh(a)
    assert a.status == ApprovalStatus.EXPIRED
    assert db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.APPROVAL_EXPIRED)) is not None


def test_one_failing_obligation_does_not_abort_the_tick_for_others(db, monkeypatch):
    user = make_user(db)
    good = make_obligation(db, user, title="good", due_at=NOW + timedelta(hours=2), status=S.ACTION_REQUIRED)
    bad = make_obligation(db, user, title="bad", due_at=NOW + timedelta(hours=2), status=S.ACTION_REQUIRED)
    for o in (good, bad):
        o.next_action_at = NOW
    db.commit()
    original = monitor.process_obligation

    def flaky(db_, ob, now, settings_, summary):
        if ob.title == "bad":
            raise RuntimeError("boom")
        return original(db_, ob, now, settings_, summary)

    monkeypatch.setattr(monitor, "process_obligation", flaky)
    s = _tick(db, NOW + timedelta(minutes=1))
    assert s.evaluated == 2 and len(s.errors) == 1
    db.refresh(good), db.refresh(bad)
    assert good.next_action_at is not None and good.next_action_at > NOW  # was processed
    assert bad.next_action_at == NOW + timedelta(minutes=1) + timedelta(minutes=5)  # backed off, not hot-looping


def test_two_concurrent_ticks_cannot_double_process_the_same_obligation(db, _sessionmaker):
    """FOR UPDATE SKIP LOCKED: while tick A holds the row, tick B skips it."""
    _, ob = _setup(db)
    at = DUE - timedelta(hours=24)
    clock.freeze(at)
    session_a, session_b = _sessionmaker(), _sessionmaker()
    # If SKIP LOCKED were ever removed, B would wait on A's row lock forever in this single thread.
    # A short lock_timeout turns that hang into a fast, explicit failure.
    session_b.execute(text("SET lock_timeout = '1500ms'"))
    try:
        a = monitor.run_tick(session_a, at, settings)  # A processes and keeps its transaction open (row locked)
        b = monitor.run_tick(session_b, at, settings)  # B runs concurrently: must skip the locked row
        session_a.commit()
        session_b.commit()
    finally:
        session_a.close()
        session_b.close()
    assert a.evaluated == 1 and b.evaluated == 0
    assert len([n for n in _notes(db, ob) if n.channel == Ch.EMAIL]) == 1
