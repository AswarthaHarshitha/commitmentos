# CommitmentOS architecture

> Living document. Statements here describe what is built *and verified*. Anything that could not be verified in this
> environment is labelled **NOT VERIFIED - external dependency unavailable**.

## The one rule that shapes everything

**The LLM understands language. Code makes decisions.**

| Responsibility | Owner |
| --- | --- |
| Is this message an obligation? What is the action? What kind of obligation? Semantic urgency? Entities? Ambiguity? The *verbatim phrase* that states the deadline ("by Friday 5pm") | LLM (extraction only, structured JSON) |
| Validating the LLM output (Pydantic), grounding it against the source text, confidence adjustments | Backend code |
| Converting a deadline *phrase* into a UTC timestamp (relative dates, weekdays, DST, business hours) | Backend code - `services/deadlines.py` |
| Confidence thresholds -> create / needs-review / candidate / ignore | Backend code |
| Deduplication, state transitions, overdue/escalation timing, reminder cadence, notification de-spam, authorization, persistence, audit | Backend code |
| Orchestration: triggers (Gmail poll, webhooks, schedules), retries, branching, delivery through Email/Telegram/Calendar, human-approval hand-off | n8n |

The LLM never sees a database, never returns a timestamp that is trusted on its own, and never triggers an external side
effect. Even a perfect prompt injection in an email can, at worst, produce a *proposal* that a human still has to accept.

## Components

```
                ┌───────────────────────── n8n (orchestrator) ─────────────────────────┐
 Gmail poll ───▶│ Commitment Detection    Deadline Monitor     Calendar Synchronization │
 Webhooks  ────▶│ Approved Actions        Completion Detection Follow-up Assistant      │
 Schedules ────▶│ Notification Dispatcher            + Error Handler                    │
                └───────┬───────────────────────────▲──────────────────────────────────┘
                        │ X-Webhook-Secret          │ header-auth webhooks
                        ▼                           │
 Browser ──▶ Next.js ──▶ FastAPI  ──────────────────┘
 (cookie)   (/api proxy)   │  extraction ──▶ LLM (Gemini | any OpenAI-compatible server)
                           │  deadlines · dedup · lifecycle · monitor · completion · audit
                           ▼
                      PostgreSQL            Mailpit (dev SMTP sink)
```

* **apps/api** - FastAPI + SQLAlchemy 2 + Alembic (4 migrations). Single process by design: the rate limiter and the test clock are
  per-process.
* **apps/web** - Next.js (App Router) + Tailwind. Talks only to `/api/*`, proxied so the browser sees one origin.
* **automation/n8n** - the eight workflows, generated from `definitions/` by `build.js`, imported and published on boot by
  the `n8n-import` service. Code-node logic lives in `code/*.js` with unit tests. See `automation/n8n/README.md`.
* **PostgreSQL** - the only system of record. n8n keeps its own database (`n8n`) in the same server.
* **e2e/** - live-stack tests: real n8n executions, real SMTP into Mailpit, real API and database.

## Contracts between n8n and the API

n8n -> API (`X-Webhook-Secret`): `/api/internal/{messages/check, extract, monitor/tick, notifications/claim, approvals/claim,
approvals/{id}, calendar/scan, followups/scan, completion/check, test-clock}` and `POST /api/webhooks/n8n` with one strictly-typed
event per kind (`run.started`, `run.finished`/`run.failed`, `message.extracted`, `notification.sent|failed`,
`approval.executed|failed`, `proposal.created`). API -> n8n (`X-CommitmentOS-Key`): the approval push webhook.

* Every handler is **idempotent**: n8n retries HTTP calls, so a duplicate event is a no-op.
* n8n **re-sends nothing the API did not ask for**: the API claims work atomically (`FOR UPDATE SKIP LOCKED`, leases), so a
  duplicate webhook or a second poll cannot run an action twice.
* The API **re-validates and re-decides everything** a workflow reports (`message.extracted` is re-analysed at commit time).
* A workflow that proposes something for a person to approve ends `WAITING`; the API resolves the run when every approval it
  proposed is decided (executed, rejected, expired, cancelled, failed).

## Two shared secrets, one per direction

`N8N_INBOUND_SECRET` authenticates n8n -> API calls (constant-time compare). `N8N_OUTBOUND_SECRET` authenticates API -> n8n webhook
triggers (n8n "Header Auth" credential). Neither can call user-facing endpoints; user JWTs cannot call `/api/internal/*`.
Secrets exist only in `.env` (git-ignored) and, encrypted, inside n8n; they never appear in workflow JSON, logs or error text.

## Time

* Everything is stored as `timestamptz` (UTC). Users have an IANA timezone; the API returns UTC instants.
* **All rules read time from `app.clock`** (or take an explicit `now`). Tests freeze it; the isolated end-to-end stack can offset it (persisted, `TEST_CLOCK=true`, never enabled next to real data) so a
  two-day reminder ladder runs in seconds without special-casing any rule.
* Relative expressions ("tomorrow") resolve against the *message's received time*, in the user's timezone, DST-aware. Several
  times in one phrase: a range ("10-11am") resolves to its start; unrelated times to the earliest, flagged for review.

## Data model

The schema is defined by the Alembic migrations in `apps/api/alembic/versions`. Integrity rules enforced **in the database**: enum CHECK constraints, `completed => completed_at`,
`due_at <=> due_precision`, unique `(user, source_type, external_id)` (idempotency ledger), unique `(user, dedupe_key)` on
notifications (anti-spam), an append-only trigger on `audit_events`.

## Failure model

Every failure has a defined, visible outcome and a bounded retry policy. Each row is exercised by a test (`e2e/` = live stack).

| Failure | What happens | Verified by |
| --- | --- | --- |
| LLM slow / overloaded (503, 429 per-minute) | n8n retries the extraction step 3x, then records a visible failure; the message can be re-sent later | `e2e/test_failures.py` (flaky, stays down) |
| LLM daily quota spent | classified `LLM_QUOTA_EXHAUSTED`, **not** retried, visible failure | e2e quota test; `test_llm_clients.py` (real captured 429 body) |
| LLM returns malformed / off-schema JSON | one repair attempt, then rejected; nothing reaches the database | e2e malformed + repaired tests; `test_extraction_*` |
| LLM hallucinated quote / date / id | grounding drops it: confident but unverifiable -> `NEEDS_REVIEW`; unknown ids dropped | e2e hallucinated test; `test_completion.py` |
| Prompt injection in a message | message is fenced as untrusted data; output is validated like any other | e2e injection test; `test_extraction_service.py` |
| Duplicate message (same id) | recognised *before* the LLM call; run `SKIPPED` | e2e duplicate test |
| Same content, different message | merged into the existing obligation by fingerprint + fuzzy score | e2e merge test; `test_dedup.py` |
| Missing / ambiguous / past deadline | `NEEDS_REVIEW` with the reasons; never silently created | `test_deadlines.py`, e2e vague test |
| Timezone / DST | deadline math in code with the user's zone | `test_deadlines.py`, e2e timezone test |
| Reminder already sent / completed / dismissed / snoozed | each rung once; nothing after completion; snooze suppresses | `e2e/test_monitor.py` |
| Notification delivery fails | recorded, retried with backoff, given up visibly; other channels unaffected | e2e Telegram-not-connected test; `test_notifications.py` |
| n8n is down | actions stay queued; the failure is recorded; a catch-up poll runs them when n8n returns | e2e n8n-outage test |
| Approved action executed twice (racing triggers) | atomic claim: exactly one execution | e2e duplicate-trigger test |
| Action proposed but not approved | cannot be executed, even by calling n8n directly | e2e pending-proposal test |
| Provider not connected (Google/Telegram) | fails safe with a reason; never reported as success | e2e Google-not-connected test |
| Any workflow error | Error Handler reports a `FAILED` run with node + scrubbed reason | e2e invalid-payload test |
| Cross-user access | 404 for other users' objects; shared system runs expose status and counts only | `test_authorization.py` |
| Database failure | `/api/health` reports `degraded`; requests get a retryable `503` (n8n's HTTP node retries it) with no SQL or message content; a half-finished ingestion rolls back completely | `test_resilience.py` |
| Expired / forged / replayed webhook or action link | 401/403; signed action tokens are single-purpose and expire | `test_webhooks.py`, `test_authorization.py` |

## What is NOT verified here

Gmail Trigger, Google Calendar and Telegram delivery need credentials that do not exist in this environment. Their nodes are
present (disabled or unconnected) with fail-safe guards; everything around them is exercised. Gemini (free tier) and a local
model served by Ollama were exercised for real; a hosted model with a paid plan was not.
