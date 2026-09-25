"""Fixtures for the live-stack tests: the Docker stack must be up (docker compose up -d)."""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from stub_llm import StubLLM

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000"
N8N = "http://localhost:5678"
MAILPIT = "http://localhost:8025"
PASSWORD = "Correct-Horse-Battery-9"
T0 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # Thu 10:00 in New York: "tomorrow 5pm" is 31 hours away, so the reminder ladder is testable


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        pytest.skip("no .env - run ./scripts/init-env.sh")
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = re.split(r"\s+#", value.strip())[0].strip().strip("'\"")
    return env


def wait_until(check, *, timeout: float = 30.0, interval: float = 0.5, what: str = "condition") -> Any:
    """Poll until `check()` returns something truthy; fail with what was being waited for.
    Transport errors while polling (a container mid-restart) count as "not yet"."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = check()
        except httpx.TransportError as exc:
            last = f"{type(exc).__name__}"
        if last and not isinstance(last, str):
            return last
        if last is True:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:g}s waiting for {what} (last value: {last!r})")


def retrying(fn, *, attempts: int = 5, delay: float = 1.0):
    """Call `fn`, retrying transport-level failures (a freshly restarted container's port mapping can drop the first requests)."""
    for attempt in range(attempts):
        try:
            return fn()
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


# ------------------------------------------------------------------------------------------------ the stack
@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return load_env()


def _healthy() -> bool:
    try:
        return (
            httpx.get(f"{API}/api/health", timeout=3).status_code == 200
            and httpx.get(f"{N8N}/healthz", timeout=3).status_code == 200
            and httpx.get(f"{MAILPIT}/api/v1/info", timeout=3).status_code == 200
        )
    except httpx.HTTPError:
        return False


def _compose(*args: str, overrides: dict[str, str] | None = None) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, env={**os.environ, **(overrides or {})}, check=True, capture_output=True, timeout=240)


def wait_for_n8n(secret: str) -> None:
    """n8n answers /healthz before its production webhooks are registered; wait until a real workflow answers (not a 404)."""
    def ready() -> bool:
        r = httpx.post(f"{N8N}/webhook/commitmentos-dispatch", json={}, headers={"X-CommitmentOS-Key": secret}, timeout=30)
        return r.status_code == 200

    wait_until(ready, timeout=120, what="n8n's webhooks to be registered")


def _wait_api() -> None:
    wait_until(lambda: httpx.get(f"{API}/api/health", timeout=3).status_code == 200, timeout=90, what="the API to become healthy")
    time.sleep(2)  # the host port mapping of a just-recreated container settles a moment after the app reports healthy


@pytest.fixture(scope="session")
def stub() -> StubLLM:
    llm = StubLLM()
    yield llm
    llm.stop()


def _retire_earlier_test_data() -> None:
    """The monitor is system-wide by design, so commitments left by earlier runs would be evaluated by every tick.
    Close the active commitments of test accounts (only accounts named e2e-*@example.com) so each run starts quiet."""
    env = load_env()
    conn = psycopg.connect(host="localhost", port=int(env.get("POSTGRES_HOST_PORT", "5433")), user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"],
                           dbname=env.get("POSTGRES_DB", "commitmentos"), autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            "update obligations set status = 'DISMISSED', dismissed_at = now(), next_action_at = null "
            "where user_id in (select id from users where email like 'e2e-%@example.com') "
            "and status in ('OPEN', 'ACTION_REQUIRED', 'SCHEDULED', 'OVERDUE', 'ESCALATED', 'NEEDS_REVIEW', 'DETECTED')"
        )
        cur.execute(
            "update notifications set status = 'CANCELLED', last_error = 'e2e cleanup' where status in ('PENDING', 'SENDING') "
            "and user_id in (select id from users where email like 'e2e-%@example.com')"
        )
    conn.close()


def stack_overrides(stub: StubLLM, **extra: str) -> dict[str, str]:
    """Environment for the api container during the tests: demo mode, and the scripted LLM instead of a real one."""
    return {
        "DEMO_MODE": "true",
        "LLM_PROVIDER": "openai_compat",
        "LLM_BASE_URL": f"http://host.docker.internal:{stub.port}/v1",
        "LLM_MODEL": "stub",
        "LLM_API_KEY": "",
        "LLM_TIMEOUT_SECONDS": "20",
        "LLM_MAX_TRANSIENT_RETRIES": "0",  # one API attempt per call: n8n's own retries are what the tests observe
        "LLM_MAX_REPAIR_ATTEMPTS": "1",
        "RATE_LIMIT_REGISTER_PER_HOUR": "1000",  # every test creates its own account
        "RATE_LIMIT_LOGIN_PER_MINUTE": "1000",
        **extra,
    }


def restart_api(stub: StubLLM, **extra: str) -> None:
    _compose("up", "-d", "--force-recreate", "api", overrides=stack_overrides(stub, **extra))
    _wait_api()


@pytest.fixture(scope="session", autouse=True)
def stack(stub: StubLLM):
    """Point the API container at the scripted LLM (and turn on demo mode) for the session; restore it afterwards."""
    if not _healthy():
        pytest.skip("the stack is not running: docker compose up -d")
    wait_for_n8n(load_env()["N8N_OUTBOUND_SECRET"])
    restart_api(stub)
    _retire_earlier_test_data()
    yield
    logs = subprocess.run(["docker", "compose", "logs", "--no-color", "--since", "1h", "api"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    (Path(__file__).parent / ".last-api.log").write_text(logs.stdout)  # what the API said during the run (git-ignored; contains no secrets)
    _compose("up", "-d", "--force-recreate", "api")
    _wait_api()


@pytest.fixture(autouse=True)
def _fresh_script(stub: StubLLM):
    stub.reset()


# ------------------------------------------------------------------------------------------------ helpers
class Db:
    def __init__(self, env: dict[str, str], name: str) -> None:
        self.conn = psycopg.connect(
            host="localhost", port=int(env.get("POSTGRES_HOST_PORT", "5433")), user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"], dbname=name, autocommit=True
        )

    def rows(self, sql: str, *args: Any) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []

    def one(self, sql: str, *args: Any) -> Any:
        rows = self.rows(sql, *args)
        return rows[0][0] if rows else None


@pytest.fixture(scope="session")
def app_db(env) -> Db:
    return Db(env, env.get("POSTGRES_DB", "commitmentos"))


@pytest.fixture(scope="session")
def n8n_db(env) -> Db:
    return Db(env, "n8n")


class N8n:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def call(self, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 120.0, secret: str | None = None) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if secret != "":
            headers["X-CommitmentOS-Key"] = secret or self.secret
        return httpx.post(f"{N8N}/webhook/{path}", json=payload if payload is not None else {}, headers=headers, timeout=timeout)


@pytest.fixture(scope="session")
def n8n(env) -> N8n:
    return N8n(env["N8N_OUTBOUND_SECRET"])


class Mail:
    def messages(self, to: str) -> list[dict[str, Any]]:
        data = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{to}", "limit": 50}, timeout=10).json()
        return sorted(data.get("messages") or [], key=lambda m: m["Created"])

    def detail(self, message_id: str) -> dict[str, Any]:
        return httpx.get(f"{MAILPIT}/api/v1/message/{message_id}", timeout=10).json()

    def subjects(self, to: str) -> list[str]:
        return [m["Subject"] for m in self.messages(to)]


@pytest.fixture(scope="session")
def mail() -> Mail:
    return Mail()


class Clock:
    def __init__(self, secret: str) -> None:
        self.headers = {"X-Webhook-Secret": secret}

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        r = httpx.post(f"{API}/api/internal/demo/clock", json=body, headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def set_to(self, at: datetime) -> dict[str, Any]:
        return self._post({"set_to": at.isoformat()})

    def advance(self, seconds: float) -> dict[str, Any]:
        return self._post({"advance_seconds": int(seconds)})

    def reset(self) -> dict[str, Any]:
        return self._post({"reset": True})

    def now(self) -> datetime:
        r = httpx.get(f"{API}/api/internal/demo/clock", headers=self.headers, timeout=10)
        return datetime.fromisoformat(r.json()["now"])


@pytest.fixture
def clock(env):
    c = Clock(env["N8N_INBOUND_SECRET"])
    c.reset()
    yield c
    c.reset()


class User:
    """A fresh account, so tests never see each other's data or mail."""

    def __init__(self, timezone: str = "America/New_York") -> None:
        self.email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
        self.timezone = timezone
        register = {"email": self.email, "password": PASSWORD, "display_name": "E2E Tester", "timezone": timezone}
        retrying(lambda: httpx.post(f"{API}/api/auth/register", json=register, timeout=10)).raise_for_status()
        token = retrying(lambda: httpx.post(f"{API}/api/auth/token", data={"username": self.email, "password": PASSWORD}, timeout=10)).json()["access_token"]
        self.http = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self.http.post(path, json=json, **kw)

    def obligations(self) -> list[dict[str, Any]]:
        body = self.get("/api/obligations", params={"limit": 100}).json()
        return body["items"] if isinstance(body, dict) else body

    def runs(self, workflow_key: str | None = None) -> list[dict[str, Any]]:
        """Every run this user may see (their own plus the shared system ones), newest first, across pages."""
        found: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {"limit": 200, "offset": offset}
            if workflow_key:
                params["workflow_key"] = workflow_key
            body = self.get("/api/automation/runs", params=params).json()
            found += body["items"]
            offset += 200
            if offset >= body["total"]:
                return found

    def run(self, n8n_execution_id: str) -> dict[str, Any] | None:
        """The history row of exactly one n8n execution (the virtual demo clock can push other rows above it, so look everywhere)."""
        return next((r for r in self.runs() if r["n8n_execution_id"] == n8n_execution_id), None)


@pytest.fixture
def user() -> User:
    return User()


# ------------------------------------------------------------------------------------------------ builders
QUOTE = "Please submit your signed internship documents by tomorrow 5pm"
BODY = f"Hi Alex,\n\nThanks for accepting the offer! {QUOTE} so we can finalise onboarding.\n\nBest,\nDana\nUniversity HR"


def extraction(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "is_obligation": True, "confidence": 0.95, "title": "Submit internship documents", "action": "Submit signed internship documents",
        "obligation_type": "DOCUMENT_REQUEST", "priority": "HIGH", "owner": "SELF", "deadline_text": "by tomorrow 5pm", "due_at": None,
        "source_context": QUOTE, "explanation": "The sender asks you to submit signed documents by 5pm tomorrow.", "ambiguity": None,
        "requires_confirmation": False, "counterparty_name": "Dana", "counterparty_email": "hr@example.org", "recurrence": None, "entities": [],
    }
    base.update(over)
    return base


def ingest_payload(user: User, external_id: str | None = None, *, body: str = BODY, received_at: datetime | None = None, **message: Any) -> dict[str, Any]:
    return {
        "user_email": user.email,
        "message": {
            "source_type": "DEMO", "external_id": external_id or f"e2e-{uuid.uuid4().hex[:10]}", "thread_id": message.pop("thread_id", None),
            "sender_email": "hr@example.org", "sender_name": "Dana Whitfield", "subject": "Internship paperwork", "body": body,
            "received_at": (received_at or datetime.now(UTC)).isoformat(), **message,
        },
    }
