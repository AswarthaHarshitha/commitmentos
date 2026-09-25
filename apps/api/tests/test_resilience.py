"""When the database fails: a clear, retryable answer, nothing half-written, and no message content in the error."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.clock import clock
from app.db import get_db
from app.models import Obligation, Source
from tests.factories import make_user
from tests.helpers_extraction import make_extraction, make_message

SECRET_TEXT = "Wire $9,999 to account 12345678 before Friday"
WEBHOOK = "/api/webhooks/n8n"


def broken(statement: str = "SELECT ... WHERE body = :body") -> OperationalError:
    return OperationalError(statement, {"body": SECRET_TEXT}, Exception("connection refused"))


class BrokenSession:
    """A session whose every operation fails like a database that has gone away."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise broken()

        return fail


@pytest.fixture
def db_down(app):
    def dead():
        yield BrokenSession()

    app.dependency_overrides[get_db] = dead
    yield
    app.dependency_overrides.pop(get_db, None)


def test_health_reports_a_database_that_is_down(client, monkeypatch):
    assert client.get("/api/health").json() == {"status": "ok", "db": "ok", "version": client.get("/api/health").json()["version"]}

    class DeadEngine:
        def connect(self):
            raise broken("SELECT 1")

    monkeypatch.setattr("app.main.get_engine", lambda: DeadEngine())
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded" and r.json()["db"] == "down"  # what a load balancer / container health check acts on


def test_a_request_that_hits_a_dead_database_gets_a_retryable_503_with_no_sql_or_content(client, n8n_headers, db_down, caplog):
    r = client.post("/api/internal/monitor/tick", headers=n8n_headers)
    assert r.status_code == 503 and r.json()["code"] == "DATABASE_UNAVAILABLE" and r.headers["retry-after"] == "5"  # n8n's HTTP node retries a 503
    assert SECRET_TEXT not in r.text and "SELECT" not in r.text and "connection refused" not in r.text
    assert SECRET_TEXT not in caplog.text and "SELECT" not in caplog.text  # the error TYPE is logged, never the statement or its parameters


def test_the_webhook_a_workflow_reports_to_is_retryable_when_the_database_is_down(client, n8n_headers, db_down):
    r = client.post(WEBHOOK, headers=n8n_headers, json={"event": "run.started", "workflow_key": "deadline-monitor", "n8n_execution_id": "77"})
    assert r.status_code == 503 and r.json()["code"] == "DATABASE_UNAVAILABLE"


def test_a_failure_halfway_through_ingesting_a_message_leaves_nothing_behind(client, n8n_headers, db, monkeypatch):
    """The message is recorded, the obligation created, and THEN queueing the notification fails: all of it must roll back,
    so n8n's retry of the same event starts from a clean slate instead of finding half a commitment."""
    clock.freeze(datetime(2026, 9, 23, 15, 5, tzinfo=UTC))
    make_user(db, "alice@example.com", timezone="America/New_York")
    db.commit()

    def explode(*args, **kwargs):
        raise broken("INSERT INTO notifications ...")

    monkeypatch.setattr("app.services.notifications.queue", explode)
    event = {"event": "message.extracted", "user_email": "alice@example.com", "message": make_message(), "extraction": make_extraction(), "n8n_execution_id": "9"}
    r = client.post(WEBHOOK, headers=n8n_headers, json=event)
    assert r.status_code == 503 and r.json()["code"] == "DATABASE_UNAVAILABLE"

    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Obligation)) == 0 and db.scalar(select(func.count()).select_from(Source)) == 0  # atomic

    monkeypatch.undo()  # the database is back; n8n retries the very same event
    ok = client.post(WEBHOOK, headers=n8n_headers, json=event)
    assert ok.status_code == 200 and ok.json()["disposition"] == "OBLIGATION_CREATED"
    assert db.scalar(select(func.count()).select_from(Obligation)) == 1  # exactly one, not two


def test_an_outage_never_turns_into_a_500_with_internals(client, n8n_headers, db_down):
    for path, method in (("/api/internal/notifications/claim", "post"), ("/api/internal/approvals/claim", "post"), ("/api/internal/monitor/tick", "post")):
        r = getattr(client, method)(path, headers=n8n_headers)
        assert r.status_code == 503 and "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()
