"""Database-level guarantees. These try to *break* the schema rather than describe it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.enums import (
    ActorType,
    AuditEventType,
    NotificationChannel,
    NotificationKind,
    ObligationStatus,
    SourceDisposition,
    SourceType,
)
from app.models import AuditEvent, Notification, Obligation, Source
from tests.conftest import TEST_DATABASE_URL, _alembic_config
from tests.factories import make_obligation, make_user

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_all_required_tables_exist(engine):
    tables = set(inspect(engine).get_table_names())
    required = {
        "users",
        "obligations",
        "sources",
        "notifications",
        "audit_events",
        "calendar_events",
        "automation_runs",
        "approval_requests",
    }
    assert required <= tables


def test_required_columns_are_indexed(engine):
    """Spec: index status, due_at, user_id, source, created_at on obligations."""
    idx = inspect(engine).get_indexes("obligations")
    indexed_first_cols = {i["column_names"][0] for i in idx}
    assert {"status", "due_at", "user_id", "source", "created_at"} <= indexed_first_cols


def test_monitor_index_is_partial_and_only_covers_active_statuses(engine):
    with engine.connect() as conn:
        definition = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_obligations_monitor'")
        ).scalar_one()
    assert "next_action_at IS NOT NULL" in definition
    assert "'COMPLETED'" not in definition and "'DISMISSED'" not in definition
    assert "'OVERDUE'" in definition


def test_invalid_enum_value_is_rejected_by_the_database(db):
    user = make_user(db)
    db.commit()
    db.execute(text("SAVEPOINT s"))
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO obligations (id, user_id, title, source, obligation_type, status, priority, "
                "confidence, fingerprint, owner, requires_confirmation, entities, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :u, 't', 'MANUAL', 'DEADLINE', 'NOT_A_STATUS', 'LOW', 1, 'f', 'me', "
                "false, '[]', now(), now())"
            ),
            {"u": user.id},
        )
    db.rollback()


def test_completed_obligation_must_have_completed_at(db):
    user = make_user(db)
    db.add(
        Obligation(
            user_id=user.id,
            title="x",
            source=SourceType.MANUAL,
            status=ObligationStatus.COMPLETED,
            fingerprint="f",
            completed_at=None,
        )
    )
    with pytest.raises(IntegrityError, match="completed_has_timestamp"):
        db.flush()


@pytest.mark.parametrize("bad", [-0.1, 1.01])
def test_confidence_must_be_between_zero_and_one(db, bad):
    user = make_user(db)
    db.add(Obligation(user_id=user.id, title="x", source=SourceType.MANUAL, fingerprint="f", confidence=bad))
    with pytest.raises(IntegrityError, match="confidence_range"):
        db.flush()


def test_due_at_and_due_precision_must_be_set_together(db):
    user = make_user(db)
    db.add(Obligation(user_id=user.id, title="x", source=SourceType.MANUAL, fingerprint="f", due_at=NOW))
    with pytest.raises(IntegrityError, match="due_precision_consistent"):
        db.flush()


def test_timestamps_round_trip_as_utc_instants(db):
    """timestamptz keeps the instant; a non-UTC input comes back as the same instant."""
    from zoneinfo import ZoneInfo

    user = make_user(db)
    local = datetime(2026, 3, 8, 9, 0, tzinfo=ZoneInfo("America/New_York"))  # EDT (DST already started)
    ob = make_obligation(db, user, due_at=local)
    db.commit()
    db.expire_all()
    loaded = db.get(Obligation, ob.id)
    assert loaded.due_at == local
    assert loaded.due_at.utcoffset() == timedelta(0)
    assert loaded.due_at.astimezone(UTC).hour == 13  # 09:00 EDT == 13:00 UTC


def test_same_message_cannot_be_recorded_twice_for_a_user(db):
    user = make_user(db)
    for _ in range(2):
        db.add(
            Source(
                user_id=user.id,
                source_type=SourceType.GMAIL,
                external_id="gmail-msg-1",
                disposition=SourceDisposition.NOT_OBLIGATION,
            )
        )
    with pytest.raises(IntegrityError, match="uq_sources_user_type_external"):
        db.flush()


def test_same_external_id_is_allowed_for_a_different_user(db):
    a, b = make_user(db), make_user(db)
    for u in (a, b):
        db.add(
            Source(
                user_id=u.id,
                source_type=SourceType.GMAIL,
                external_id="gmail-msg-1",
                disposition=SourceDisposition.NOT_OBLIGATION,
            )
        )
    db.flush()  # must not raise


def test_notification_dedupe_key_is_unique_per_user(db):
    user = make_user(db)
    ob = make_obligation(db, user)

    def n():
        return Notification(
            user_id=user.id,
            obligation_id=ob.id,
            kind=NotificationKind.REMINDER,
            channel=NotificationChannel.EMAIL,
            title="t",
            body="b",
            dedupe_key=f"{ob.id}:REMINDER:1:EMAIL",
        )

    db.add(n())
    db.flush()
    db.add(n())
    with pytest.raises(IntegrityError, match="uq_notifications_user_dedupe"):
        db.flush()


def _add_audit(db, user):
    ev = AuditEvent(
        user_id=user.id,
        event_type=AuditEventType.COMMITMENT_DETECTED,
        actor_type=ActorType.SYSTEM,
        message="detected",
    )
    db.add(ev)
    db.flush()
    return ev


def test_audit_log_rejects_update(db):
    user = make_user(db)
    ev = _add_audit(db, user)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE audit_events SET message = 'tampered' WHERE id = :i"), {"i": ev.id})
    db.rollback()


def test_audit_log_rejects_delete(db):
    user = make_user(db)
    ev = _add_audit(db, user)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": ev.id})
    db.rollback()


def test_audit_events_are_ordered_by_identity_and_use_the_app_clock(db):
    from app.clock import clock

    user = make_user(db)
    clock.freeze(NOW)
    first = _add_audit(db, user)
    clock.advance(timedelta(hours=5))
    second = _add_audit(db, user)
    assert second.id > first.id
    assert second.created_at - first.created_at == timedelta(hours=5)


def test_deleting_a_user_with_audit_history_is_blocked(db):
    user = make_user(db)
    _add_audit(db, user)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
    db.rollback()


def test_obligation_children_cascade_when_obligation_is_removed(db):
    user = make_user(db)
    ob = make_obligation(db, user)
    db.add(
        Source(
            user_id=user.id,
            obligation_id=ob.id,
            source_type=SourceType.GMAIL,
            external_id="m1",
            disposition=SourceDisposition.OBLIGATION_CREATED,
        )
    )
    db.commit()
    db.execute(text("DELETE FROM obligations WHERE id = :i"), {"i": ob.id})
    db.commit()
    assert db.execute(text("SELECT count(*) FROM sources")).scalar_one() == 0


def test_migrations_are_reversible():
    """upgrade -> downgrade base -> upgrade must leave a working schema."""
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    from sqlalchemy import create_engine

    eng = create_engine(TEST_DATABASE_URL)
    try:
        assert "obligations" in inspect(eng).get_table_names()
        with eng.connect() as conn:
            triggers = conn.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_audit_events_append_only'")
            ).scalar_one()
            assert triggers == 1
    finally:
        eng.dispose()
