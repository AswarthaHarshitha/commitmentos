"""Fixtures for the live-stack tests. The suite builds its own throw-away stack (see harness.py); it never touches the real one."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from harness import (  # noqa: F401  (re-exported for the test modules)
    API,
    BODY,
    ENV_FILE,
    MAILPIT,
    N8N,
    PASSWORD,
    PROJECT,
    QUOTE,
    ROOT,
    T0,
    Clock,
    Db,
    Mail,
    N8n,
    User,
    bring_up_stack,
    compose,
    docker_available,
    extraction,
    ingest_payload,
    load_env,
    restart_api,
    retrying,
    stack_is_up,
    tear_down_stack,
    wait_api,
    wait_for_n8n,
    wait_until,
)
from stub_llm import StubLLM


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    try:
        return load_env()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def stub() -> StubLLM:
    llm = StubLLM()
    yield llm
    llm.stop()


@pytest.fixture(scope="session", autouse=True)
def stack(stub: StubLLM, env):
    """Build the isolated test stack from an empty database, pointed at the scripted LLM; remove it (and its volumes) afterwards.
    Set E2E_KEEP_STACK=1 to leave it running for inspection."""
    if not docker_available():
        pytest.skip("docker is not available")
    bring_up_stack(stub)
    yield
    logs = subprocess.run(["docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE), "logs", "--no-color", "api"], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    (Path(__file__).parent / ".last-api.log").write_text(logs.stdout)  # what the API said during the run (git-ignored; contains no secrets)
    if not os.environ.get("E2E_KEEP_STACK"):
        tear_down_stack()


@pytest.fixture(autouse=True)
def _fresh_script(stub: StubLLM):
    stub.reset()


@pytest.fixture(scope="session")
def app_db(env) -> Db:
    return Db(env, env.get("POSTGRES_DB", "commitmentos"))


@pytest.fixture(scope="session")
def n8n_db(env) -> Db:
    return Db(env, "n8n")


@pytest.fixture(scope="session")
def n8n(env) -> N8n:
    return N8n(env["N8N_OUTBOUND_SECRET"])


@pytest.fixture(scope="session")
def mail() -> Mail:
    return Mail()


@pytest.fixture
def clock(env):
    c = Clock(env["N8N_INBOUND_SECRET"])
    c.reset()
    yield c
    c.reset()


@pytest.fixture
def user() -> User:
    return User()
