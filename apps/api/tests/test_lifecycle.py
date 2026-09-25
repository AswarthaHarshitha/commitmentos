"""The obligation state machine: every (from, to) pair is checked, not just the happy path."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.enums import (
    ApprovalAction,
    ApprovalStatus,
    AuditEventType,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import InvalidTransition
from app.models import ApprovalRequest, AuditEvent, Notification
from app.services import lifecycle
from app.services.audit import Actor
from tests.factories import make_obligation, make_user

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SYSTEM = Actor.system("test")

ALL_PAIRS = list(itertools.product(list(S), list(S)))


@pytest.mark.parametrize("src,dst", [(a, b) for a, b in ALL_PAIRS if a != b])
def test_every_transition_pair_matches_the_documented_table(db, src, dst):
    user = make_user(db)
    ob = make_obligation(db, user, status=src, due_at=NOW + timedelta(days=1))
    if lifecycle.can_transition(src, dst):
        assert lifecycle.transition(db, ob, dst, actor=SYSTEM, now=NOW) is True
        assert ob.status == dst
    else:
        with pytest.raises(InvalidTransition):
            lifecycle.transition(db, ob, dst, actor=SYSTEM, now=NOW)
        assert ob.status == src  # untouched on failure


def test_transition_to_the_same_status_is_a_silent_no_op(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.OPEN)
    assert lifecycle.transition(db, ob, S.OPEN, actor=SYSTEM, now=NOW) is False
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.obligation_id == ob.id)) is None


def test_completing_sets_timestamp_clears_schedule_and_writes_audit(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.ACTION_REQUIRED, due_at=NOW + timedelta(hours=3))
    ob.next_action_at = NOW + timedelta(hours=1)
    ob.snoozed_until = NOW + timedelta(hours=2)
    lifecycle.transition(db, ob, S.COMPLETED, actor=Actor.user(user), now=NOW, via="DASHBOARD")
    assert ob.completed_at == NOW
    assert ob.completed_via == "DASHBOARD"
    assert ob.next_action_at is None and ob.snoozed_until is None
    event = db.scalar(select(AuditEvent).where(AuditEvent.obligation_id == ob.id, AuditEvent.event_type == AuditEventType.COMPLETED))
    assert event is not None
    assert event.data["from"] == "ACTION_REQUIRED" and event.data["via"] == "DASHBOARD"


def _queue(db, user, ob, kind, status, key):
    n = Notification(
        user_id=user.id, obligation_id=ob.id, kind=kind, channel=NotificationChannel.EMAIL,
        status=status, title="t", body="b", dedupe_key=key,
    )
    db.add(n)
    db.flush()
    return n


def test_completing_cancels_queued_reminders_but_keeps_sent_history_and_action_results(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.ACTION_REQUIRED, due_at=NOW + timedelta(hours=3))
    pending = _queue(db, user, ob, NotificationKind.REMINDER, NotificationStatus.PENDING, "k1")
    sending = _queue(db, user, ob, NotificationKind.HIGH_PRIORITY_REMINDER, NotificationStatus.SENDING, "k2")
    sent = _queue(db, user, ob, NotificationKind.REMINDER, NotificationStatus.SENT, "k3")
    result = _queue(db, user, ob, NotificationKind.ACTION_RESULT, NotificationStatus.PENDING, "k4")

    lifecycle.transition(db, ob, S.COMPLETED, actor=Actor.user(user), now=NOW, via="API")
    db.refresh(pending), db.refresh(sending), db.refresh(sent), db.refresh(result)

    assert pending.status == NotificationStatus.CANCELLED
    assert sending.status == NotificationStatus.CANCELLED
    assert sent.status == NotificationStatus.SENT  # history is never rewritten
    assert result.status == NotificationStatus.PENDING  # informational, still delivered
    stopped = db.scalar(select(AuditEvent).where(AuditEvent.event_type == AuditEventType.REMINDERS_STOPPED))
    assert stopped is not None and stopped.data["count"] == 2


def test_closing_withdraws_pending_approvals_but_not_ones_the_user_already_approved(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.OPEN)
    waiting = ApprovalRequest(user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.SEND_FOLLOW_UP, title="w", payload={})
    approved = ApprovalRequest(
        user_id=user.id, obligation_id=ob.id, action_type=ApprovalAction.CREATE_CALENDAR_EVENT, title="a",
        payload={}, status=ApprovalStatus.APPROVED,
    )
    db.add_all([waiting, approved])
    db.flush()
    lifecycle.transition(db, ob, S.DISMISSED, actor=Actor.user(user), now=NOW)
    db.refresh(waiting), db.refresh(approved)
    assert waiting.status == ApprovalStatus.CANCELLED
    assert approved.status == ApprovalStatus.APPROVED  # an explicit user instruction is honoured


def test_reopening_clears_completion_fields_so_the_db_constraint_holds(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.OPEN)
    lifecycle.transition(db, ob, S.COMPLETED, actor=SYSTEM, now=NOW, via="API")
    lifecycle.transition(db, ob, S.OPEN, actor=SYSTEM, now=NOW + timedelta(hours=1))
    db.flush()  # would raise if completed_at were left set on a non-completed row... or unset on a completed one
    assert ob.completed_at is None and ob.completed_via is None
    kinds = [e.event_type for e in db.scalars(select(AuditEvent).where(AuditEvent.obligation_id == ob.id).order_by(AuditEvent.id))]
    assert kinds == [AuditEventType.COMPLETED, AuditEventType.REOPENED]


def test_accepting_a_detection_is_audited_as_accepted(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.NEEDS_REVIEW)
    lifecycle.transition(db, ob, S.OPEN, actor=Actor.user(user), now=NOW)
    ev = db.scalar(select(AuditEvent).where(AuditEvent.obligation_id == ob.id))
    assert ev.event_type == AuditEventType.ACCEPTED


def test_invalid_transition_reports_both_states(db):
    user = make_user(db)
    ob = make_obligation(db, user, status=S.COMPLETED)
    with pytest.raises(InvalidTransition) as exc:
        lifecycle.transition(db, ob, S.OVERDUE, actor=SYSTEM, now=NOW)
    assert exc.value.extra == {"from": "COMPLETED", "to": "OVERDUE"}
