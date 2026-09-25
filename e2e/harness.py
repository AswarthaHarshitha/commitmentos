"""Plain helpers for the end-to-end tests (no pytest here).

The suite runs against its OWN throw-away stack - a separate compose project with its own database, ports, secrets and a blank
language-model key - and never against the stack that holds a person's real data. It builds that stack, waits for it, and
tears it down (with its volumes) afterwards.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from stub_llm import StubLLM

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "commitmentos-e2e"  # the compose project: every container, network and volume of the test stack carries this name
ENV_FILE = ROOT / ".env.e2e"  # git-ignored; never the real .env
PORTS = {"API_PORT": "18000", "N8N_PORT": "15678", "POSTGRES_HOST_PORT": "15433", "MAILPIT_UI_PORT": "18025", "MAILPIT_SMTP_PORT": "11025", "WEB_PORT": "13000"}
API = f"http://localhost:{PORTS['API_PORT']}"
N8N = f"http://localhost:{PORTS['N8N_PORT']}"
MAILPIT = f"http://localhost:{PORTS['MAILPIT_UI_PORT']}"
SECRET_KEYS = ("POSTGRES_PASSWORD", "JWT_SECRET", "N8N_INBOUND_SECRET", "N8N_OUTBOUND_SECRET", "N8N_ENCRYPTION_KEY")
PASSWORD = "Correct-Horse-Battery-9"
T0 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # Thu 10:00 in New York: "tomorrow 5pm" is 31 hours away, so the reminder ladder is testable


def _parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = re.split(r"\s+#", value.strip())[0].strip().strip("'\"")
    return env


def ensure_env_file() -> None:
    """Write .env.e2e once: the template's defaults, test-only ports, fresh random secrets, and NO language-model key.
    Everything that could point at real data or spend a real quota is set to something else on purpose."""
    if ENV_FILE.exists():
        return
    template = (ROOT / ".env.example").read_text().splitlines()
    fixed = {
        **PORTS,
        "APP_ENV": "development",
        "POSTGRES_DB": "commitmentos_e2e",
        "PUBLIC_WEB_URL": f"http://localhost:{PORTS['WEB_PORT']}",
        "TEST_CLOCK": "true",
        "N8N_SAVE_SUCCESS_EXECUTIONS": "all",  # the tests read n8n's executions to prove the workflows really ran
        "LLM_PROVIDER": "openai_compat",
        "GEMINI_API_KEY": "",
        "SMTP_HOST": "mailpit",  # the suite mails invented addresses: only ever the bundled local inbox
        "SMTP_USER": "",
        "SMTP_PASSWORD": "",
        "SMTP_SECURE": "false",
        "ALLOW_REGISTRATION": "true",
        "TELEGRAM_BOT_TOKEN": "",
        **{key: secrets.token_hex(32) for key in SECRET_KEYS},
    }
    lines, seen = [], set()
    for line in template:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in fixed:
            lines.append(f"{key}={fixed[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines += [f"{key}={value}" for key, value in fixed.items() if key not in seen]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def load_env() -> dict[str, str]:
    """The test stack's settings - and a hard stop if they could ever be the real stack's."""
    ensure_env_file()
    env = _parse_env(ENV_FILE)
    real = _parse_env(ROOT / ".env") if (ROOT / ".env").exists() else {}
    for key in ("API_PORT", "N8N_PORT", "POSTGRES_HOST_PORT", "MAILPIT_UI_PORT", "POSTGRES_PASSWORD", "N8N_ENCRYPTION_KEY", "JWT_SECRET"):
        if key in real and env.get(key) == real[key]:
            raise RuntimeError(f"refusing to run: {key} in .env.e2e equals the real .env - the end-to-end suite must use its own stack")
    if env.get("GEMINI_API_KEY"):
        raise RuntimeError("refusing to run: .env.e2e holds a language-model key; the suite uses a scripted stand-in and must never spend a real quota")
    if env.get("SMTP_USER") or env.get("SMTP_PASSWORD") or env.get("SMTP_HOST", "mailpit") != "mailpit":
        raise RuntimeError("refusing to run: .env.e2e points at a real mail server; the suite sends mail to invented addresses and must only ever reach the local test inbox")
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
def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def stack_is_up() -> bool:
    try:
        return (
            httpx.get(f"{API}/api/health", timeout=3).status_code == 200
            and httpx.get(f"{N8N}/healthz", timeout=3).status_code == 200
            and httpx.get(f"{MAILPIT}/api/v1/info", timeout=3).status_code == 200
        )
    except httpx.HTTPError:
        return False


def _scrubbed_environ() -> dict[str, str]:
    """The caller's environment minus every configuration key the stack reads.

    Compose gives shell variables priority over --env-file. Someone who ran `source scripts/dev-env.sh` has the REAL stack's ports,
    secrets and model key exported, and without this the test stack would silently be built from them."""
    keys = set(_parse_env(ROOT / ".env.example")) | set(_parse_env(ENV_FILE))
    if (ROOT / ".env").exists():
        keys |= set(_parse_env(ROOT / ".env"))
    keys |= {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_ENV_FILES", "COMPOSE_PROFILES"}
    return {k: v for k, v in os.environ.items() if k not in keys}


def compose(*args: str, overrides: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    """`docker compose` against the TEST project and the TEST env file only."""
    command = ["docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE), *args]
    result = subprocess.run(command, cwd=ROOT, env={**_scrubbed_environ(), **(overrides or {})}, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:  # docker's own words are the useful part; a bare CalledProcessError hides them
        raise RuntimeError(f"`docker compose {' '.join(args)}` failed ({result.returncode}):\n{(result.stderr or result.stdout)[-3000:]}")
    return result


def bring_up_stack(stub: StubLLM) -> None:
    """Build and start the test stack from nothing (empty database volume), pointed at the scripted model."""
    env = load_env()
    compose("up", "-d", "--build", "--wait", overrides=stack_overrides(stub))
    wait_api()
    wait_for_n8n(env["N8N_OUTBOUND_SECRET"])
    clock = httpx.get(f"{API}/api/internal/test-clock", headers={"X-Webhook-Secret": env["N8N_INBOUND_SECRET"]}, timeout=10)
    if clock.status_code != 200:  # only the test stack has the test clock; anything else is not the stack this suite may touch
        raise RuntimeError("refusing to run: the API on the test port does not have the test clock enabled, so it is not the test stack")


def tear_down_stack() -> None:
    compose("down", "--volumes", "--remove-orphans", timeout=300)


def wait_for_n8n(secret: str) -> None:
    """n8n answers /healthz before its production webhooks are registered; wait until a real workflow answers (not a 404)."""
    def ready() -> bool:
        r = httpx.post(f"{N8N}/webhook/commitmentos-dispatch", json={}, headers={"X-CommitmentOS-Key": secret}, timeout=30)
        return r.status_code == 200

    wait_until(ready, timeout=120, what="n8n's webhooks to be registered")


def wait_api() -> None:
    wait_until(lambda: httpx.get(f"{API}/api/health", timeout=3).status_code == 200, timeout=90, what="the API to become healthy")
    time.sleep(2)  # the host port mapping of a just-recreated container settles a moment after the app reports healthy


def stack_overrides(stub: StubLLM, **extra: str) -> dict[str, str]:
    """Environment for the api container during the tests: the test clock, and the scripted LLM instead of a real one."""
    return {
        "TEST_CLOCK": "true",
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
    compose("up", "-d", "--force-recreate", "api", overrides=stack_overrides(stub, **extra))
    wait_api()



class Db:
    def __init__(self, env: dict[str, str], name: str) -> None:
        self.conn = psycopg.connect(
            host="localhost", port=int(env.get("POSTGRES_HOST_PORT", PORTS["POSTGRES_HOST_PORT"])), user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"], dbname=name, autocommit=True
        )

    def rows(self, sql: str, *args: Any) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []

    def one(self, sql: str, *args: Any) -> Any:
        rows = self.rows(sql, *args)
        return rows[0][0] if rows else None


class N8n:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def call(self, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 120.0, secret: str | None = None) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if secret != "":
            headers["X-CommitmentOS-Key"] = secret or self.secret
        return httpx.post(f"{N8N}/webhook/{path}", json=payload if payload is not None else {}, headers=headers, timeout=timeout)


class Mail:
    def messages(self, to: str) -> list[dict[str, Any]]:
        data = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{to}", "limit": 50}, timeout=10).json()
        return sorted(data.get("messages") or [], key=lambda m: m["Created"])

    def detail(self, message_id: str) -> dict[str, Any]:
        return httpx.get(f"{MAILPIT}/api/v1/message/{message_id}", timeout=10).json()

    def subjects(self, to: str) -> list[str]:
        return [m["Subject"] for m in self.messages(to)]


class Clock:
    def __init__(self, secret: str) -> None:
        self.headers = {"X-Webhook-Secret": secret}

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        r = httpx.post(f"{API}/api/internal/test-clock", json=body, headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def set_to(self, at: datetime) -> dict[str, Any]:
        return self._post({"set_to": at.isoformat()})

    def advance(self, seconds: float) -> dict[str, Any]:
        return self._post({"advance_seconds": int(seconds)})

    def reset(self) -> dict[str, Any]:
        return self._post({"reset": True})

    def now(self) -> datetime:
        r = httpx.get(f"{API}/api/internal/test-clock", headers=self.headers, timeout=10)
        return datetime.fromisoformat(r.json()["now"])


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

    def patch(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self.http.patch(path, json=json, **kw)

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
        """The history row of exactly one n8n execution (the virtual clock can push other rows above it, so look everywhere)."""
        return next((r for r in self.runs() if r["n8n_execution_id"] == n8n_execution_id), None)


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
            "source_type": "WEBHOOK", "external_id": external_id or f"e2e-{uuid.uuid4().hex[:10]}", "thread_id": message.pop("thread_id", None),
            "sender_email": "hr@example.org", "sender_name": "Dana Whitfield", "subject": "Internship paperwork", "body": body,
            "received_at": (received_at or datetime.now(UTC)).isoformat(), **message,
        },
    }
