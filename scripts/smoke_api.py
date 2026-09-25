#!/usr/bin/env python3
"""End-to-end smoke test of the RUNNING API (no mocks): auth, lifecycle, authorization, webhook auth.

    source scripts/dev-env.sh && apps/api/.venv/bin/python scripts/smoke_api.py [http://localhost:8000]

Prints every step with the real status code. Exits non-zero on the first surprise.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else f"http://localhost:{os.environ.get('API_PORT', '8000')}").rstrip("/")
SECRET = os.environ["N8N_INBOUND_SECRET"]
PASSWORD = "smoke-test-password-123"
stamp = int(time.time())


def step(label: str, response: httpx.Response, expect: int | tuple[int, ...]) -> httpx.Response:
    ok = response.status_code in ((expect,) if isinstance(expect, int) else expect)
    print(f"{'PASS' if ok else 'FAIL'}  {response.status_code}  {label}")
    if not ok:
        print("      body:", response.text[:400])
        sys.exit(1)
    return response


def check(label: str, condition: bool) -> None:
    print(f"{'PASS' if condition else 'FAIL'}       {label}")
    if not condition:
        sys.exit(1)


alice = httpx.Client(base_url=BASE, timeout=15, headers={"Origin": "http://localhost:3000"})
bob = httpx.Client(base_url=BASE, timeout=15, headers={"Origin": "http://localhost:3000"})
anon = httpx.Client(base_url=BASE, timeout=15)

r = step("health", anon.get("/api/health"), 200)
check("health reports db ok", r.json()["db"] == "ok")

r = step("register alice", alice.post("/api/auth/register", json={
    "email": f"alice+{stamp}@example.com", "password": PASSWORD, "display_name": "Alice Smoke", "timezone": "America/New_York"}), 201)
cookie = r.headers["set-cookie"].lower()
check("session cookie is HttpOnly + SameSite=Lax", "httponly" in cookie and "samesite=lax" in cookie)
check("password/hash never returned", "password" not in r.text.lower() and "argon" not in r.text.lower())

step("me", alice.get("/api/auth/me"), 200)
step("anonymous is refused", anon.get("/api/obligations"), 401)

r = step("create obligation (local 17:00 New York)", alice.post("/api/obligations", json={
    "title": "Submit internship documents", "obligation_type": "DOCUMENT_REQUEST", "priority": "HIGH",
    "counterparty_name": "University HR", "counterparty_email": "hr@example.org",
    "due": {"date": "2026-12-15", "time": "17:00"}}), 201)
ob = r.json()
check("deadline stored as UTC (17:00 EST = 22:00Z)", ob["due_at"] == "2026-12-15T22:00:00Z")
check("monitor scheduled 24h before the deadline", ob["next_action_at"] == "2026-12-14T22:00:00Z")

step("validation: naive timestamp refused", alice.post("/api/obligations", json={"title": "x", "due_at": "2026-12-15T17:00:00"}), 422)
step("validation: mass assignment refused", alice.post("/api/obligations", json={"title": "x", "status": "COMPLETED"}), 422)
step("csrf: foreign Origin blocked", alice.post("/api/obligations", json={"title": "x"}, headers={"Origin": "https://evil.example.org"}), 403)

step("list", alice.get("/api/obligations"), 200)
d = step("detail", alice.get(f"/api/obligations/{ob['id']}"), 200).json()
check("suggested next action present", bool(d["suggested_next_action"]["code"]))
t = step("timeline", alice.get(f"/api/obligations/{ob['id']}/timeline"), 200).json()
check("timeline has past + planned entries", {e["kind"] for e in t} == {"past", "planned"})

step("register bob", bob.post("/api/auth/register", json={"email": f"bob+{stamp}@example.com", "password": PASSWORD}), 201)
step("bob cannot read alice's obligation", bob.get(f"/api/obligations/{ob['id']}"), 404)
step("bob cannot complete alice's obligation", bob.post(f"/api/obligations/{ob['id']}/complete"), 404)
check("bob's list is empty", bob.get("/api/obligations").json()["total"] == 0)

step("webhook without secret", anon.post("/api/webhooks/n8n", json={"event": "run.started", "workflow_key": "deadline-monitor", "n8n_execution_id": f"smoke-{stamp}"}), 401)
step("webhook with wrong secret", anon.post("/api/webhooks/n8n", headers={"X-Webhook-Secret": "nope"}, json={"event": "run.started", "workflow_key": "deadline-monitor", "n8n_execution_id": f"smoke-{stamp}"}), 401)
hdr = {"X-Webhook-Secret": SECRET}
step("webhook run.started", anon.post("/api/webhooks/n8n", headers=hdr, json={"event": "run.started", "workflow_key": "deadline-monitor", "n8n_execution_id": f"smoke-{stamp}", "trigger": "SCHEDULE"}), 200)
step("webhook run.finished", anon.post("/api/webhooks/n8n", headers=hdr, json={"event": "run.finished", "n8n_execution_id": f"smoke-{stamp}", "status": "SUCCESS", "result": {"evaluated": 0}}), 200)
step("webhook rejects unknown fields", anon.post("/api/webhooks/n8n", headers=hdr, json={"event": "run.finished", "n8n_execution_id": "x", "oops": 1}), 422)
tick = step("monitor tick (n8n-only endpoint)", anon.post("/api/internal/monitor/tick", headers=hdr), 200).json()
check("tick evaluated nothing yet (deadline is months away)", tick["evaluated"] == 0 and tick["errors"] == [])
step("user session cannot call internal endpoints", alice.post("/api/internal/monitor/tick"), 401)

runs = step("automation runs visible to the user", alice.get("/api/automation/runs"), 200).json()
check("the reported run is listed with n8n's execution id", any(x["n8n_execution_id"] == f"smoke-{stamp}" and x["status"] == "SUCCESS" for x in runs["items"]))

c1 = step("complete", alice.post(f"/api/obligations/{ob['id']}/complete"), 200).json()
c2 = step("complete again (idempotent)", alice.post(f"/api/obligations/{ob['id']}/complete"), 200).json()
check("second completion changed nothing", c1["changed"] is True and c2["changed"] is False)
check("reminders stopped: no next action scheduled", c1["obligation"]["next_action_at"] is None)

audit = step("audit trail", alice.get("/api/audit"), 200).json()["items"]
kinds = [e["event_type"] for e in reversed(audit)]
print("      audit:", " -> ".join(kinds))
check("audit shows creation then completion exactly once", kinds.count("COMPLETED") == 1 and kinds.index("OBLIGATION_CREATED") < kinds.index("COMPLETED"))

step("dashboard", alice.get("/api/dashboard"), 200)
step("logout", alice.post("/api/auth/logout"), 204)
step("session is dead after logout", alice.get("/api/auth/me"), 401)
print("\nALL SMOKE CHECKS PASSED against", BASE)
