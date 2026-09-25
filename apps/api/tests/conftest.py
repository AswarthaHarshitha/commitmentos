"""Test harness.

Tests run against a REAL PostgreSQL database (the compose `postgres` service, database
`commitmentos_test`), because the guarantees we care about - unique constraints, CHECK
constraints, the append-only audit trigger, timestamptz semantics, FOR UPDATE SKIP LOCKED -
do not exist in SQLite. The schema is built by running the actual Alembic migrations, so
the migrations themselves are exercised on every test run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_TEST_URL = "postgresql+psycopg://commitmentos:commitmentos@localhost:5433/commitmentos_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_URL)

# Must be set before app.config.get_settings() is first called.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("N8N_INBOUND_SECRET", "test-inbound-secret-0123456789abcdef")
os.environ.setdefault("N8N_OUTBOUND_SECRET", "test-outbound-secret-0123456789abcdef")
os.environ["LLM_PROVIDER"] = "none"  # tests inject fake LLM clients explicitly
# Hermetic by construction: a developer's real keys (e.g. from `source scripts/dev-env.sh`) must never reach a test.
for _secret in ("GEMINI_API_KEY", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD"):
    os.environ[_secret] = ""
os.environ["TEST_CLOCK"] = "false"
os.environ["DEFAULT_TIMEZONE"] = "UTC"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

API_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.attributes["dburl"] = TEST_DATABASE_URL
    return cfg


@pytest.fixture(scope="session")
def engine():
    url = make_url(TEST_DATABASE_URL)
    # Safety: this fixture DROPs the public schema. Never point it at a real database.
    assert url.database and url.database.endswith("_test"), (
        f"refusing to run destructive test setup against database {url.database!r}; "
        "TEST_DATABASE_URL must point at a database whose name ends with '_test'"
    )
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(_alembic_config(), "head")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def _sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


@pytest.fixture(autouse=True)
def _clean_database(engine):
    """Empty every table before each test (TRUNCATE bypasses the audit row-trigger by design)."""
    yield
    with engine.begin() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")
        ).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _reset_clock():
    from app.clock import clock

    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def db(_sessionmaker) -> Iterator[Session]:
    session = _sessionmaker()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --------------------------------------------------------------------------- API-level fixtures
@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.deps import reset_limiters

    reset_limiters()
    yield
    reset_limiters()


class FakeN8n:
    """Stands in for the API -> n8n client: records triggers, can be told to fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_with: str | None = None
        self.options: dict = {}

    def trigger(self, path: str, payload: dict, **options):
        from app.services.n8n_client import TriggerResult

        self.calls.append((path, payload))
        self.options = options
        if self.fail_with:
            return TriggerResult(False, None, self.fail_with, 3)
        return TriggerResult(True, 200, None, 1)


@pytest.fixture
def app():
    from app.main import app as fastapi_app

    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def fake_n8n(app) -> FakeN8n:
    from app.services.n8n_client import get_n8n_client

    fake = FakeN8n()
    app.dependency_overrides[get_n8n_client] = lambda: fake
    return fake


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://localhost:3000") as c:
        yield c


@pytest.fixture
def make_client(app):
    """Factory for independent cookie jars (independent 'browsers')."""
    from fastapi.testclient import TestClient

    opened = []

    def _make() -> TestClient:
        c = TestClient(app, base_url="http://localhost:3000")
        c.__enter__()
        opened.append(c)
        return c

    yield _make
    for c in opened:
        c.__exit__(None, None, None)


PASSWORD = "correct horse battery staple"


def register(client, email: str = "alice@example.com", *, timezone: str = "America/New_York", name: str = "Alice Example"):
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "timezone": timezone, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def alice(client):
    register(client, "alice@example.com", name="Alice Example")
    return client


@pytest.fixture
def bob(make_client):
    c = make_client()
    register(c, "bob@example.com", name="Bob Example")
    return c


@pytest.fixture
def n8n_headers():
    return {"X-Webhook-Secret": os.environ["N8N_INBOUND_SECRET"]}


@pytest.fixture
def fake_llm(app):
    """Override the LLM dependency with a scriptable fake. Usage: llm = fake_llm(make_extraction(...), ...)"""
    from app.services.extraction.llm import get_llm_client
    from tests.helpers_extraction import FakeLLM

    def install(*script):
        fake = FakeLLM(*script)
        app.dependency_overrides[get_llm_client] = lambda: fake
        return fake

    return install
