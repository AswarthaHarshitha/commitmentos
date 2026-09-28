# CommitmentOS

An operating system for the things you promised, owe, need to act on, or must remember - detected from your
email, tracked to a deadline computed by code (never guessed by a model), and followed up until it is done.

## What it does

Paste an email, or (once you've authorized it) let Gmail hand one over: CommitmentOS reads it, decides whether it
states a commitment, works out who owes whom what and by when, and tracks it - reminding you as the deadline
approaches, marking it overdue if it passes, and proposing (never sending outright) a follow-up email or a
calendar event for you to approve. Every step is visible: what the system understood, the exact words it relied
on, and a timeline of everything that happened and everything still planned.

## Core architecture

**The language model reads. Code decides.** A model turns a message into structured fields (is this a commitment?
what kind? what does the deadline phrase say, verbatim?); everything that matters - the actual deadline
arithmetic, thresholds, deduplication, escalation timing, authorization, persistence, audit - is ordinary, tested
code that never trusts the model's output blindly. n8n orchestrates *when* things run and hands off to the API for
every decision; the API validates, computes and decides; Postgres is the only system of record.

Full write-up, contracts between n8n and the API, and the failure-mode table: **[docs/architecture.md](docs/architecture.md)**.

```
 Gmail / pasted email ──▶ n8n (Commitment Detection) ──▶ API (validate, ground, decide) ──▶ Postgres
                                     │                                                          │
                     Deadline Monitor · Notification Dispatcher · Calendar Sync · Follow-up ◀────┘
                                     │
                              Email / Telegram, only after you approve
```

## Features

- Commitment detection from email, with a human-readable "why this was detected" and the exact source quote.
- Deterministic deadline resolution (relative dates, weekdays, DST, business hours) - the model never returns a
  timestamp that is trusted on its own.
- A 24h / 6h / overdue / escalated reminder ladder, each rung sent once, silenced once you act.
- Nothing with an external effect (a follow-up email, a calendar event) happens without your explicit approval.
- Follow-up emails are addressed only to the address the original message came from - never one a model merely
  mentions in the text, and not editable to anywhere else.
- A full audit trail (append-only) and an automations console showing every workflow run.
- Snooze, dismiss, reopen, manual commitments, recurring commitments.

## Technology

- **Frontend**: Next.js (App Router), TypeScript, Tailwind, Framer Motion, TanStack Query, Radix.
- **API**: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Pydantic.
- **Orchestration**: n8n (self-hosted), workflows generated from source (`automation/n8n`) with unit-tested
  Code-node logic.
- **Language model**: Gemini (native REST) or any OpenAI-compatible server (Ollama, vLLM, ...), or none.

## Local development

```
git clone <this repo> && cd commitmentos
./scripts/init-env.sh              # creates .env from .env.example, fills secrets with random values
docker compose up -d --build       # postgres, mailpit, n8n (imported + activated), api, web
```

- App: http://localhost:3000 · API docs: http://localhost:8000/docs · n8n: http://localhost:5678 · Mailpit
  (catches every outgoing email locally): http://localhost:8025
- Register an account in the app; email/reminders land in Mailpit until real SMTP is configured (below).

Backend tests (against a real, throwaway Postgres database):
```
source scripts/dev-env.sh
apps/api/.venv/bin/python -m pytest apps/api -q
```
n8n workflow-code unit tests: `node --test automation/n8n/tests/*.test.js`. Frontend unit tests:
`cd apps/web && npm test`. End-to-end tests against a **fully isolated** stack (its own database, ports, secrets -
never the one above): `apps/api/.venv/bin/python -m pytest e2e -q` (see `e2e/README.md`).

## Environment variables

Every variable is documented inline in **`.env.example`** - copy it to `.env` (or let `init-env.sh` do it) and fill
in what you need. `.env` is git-ignored and must never be committed; only placeholders belong in `.env.example`.

## Google OAuth / Gmail integration

There is no custom Google OAuth flow in this codebase. Gmail access is n8n's own built-in **Gmail Trigger** node:
you authorize it once, inside n8n's UI (Settings → Credentials → New → Gmail Trigger, which opens Google's own
consent screen for a Google Cloud OAuth client you create). The node ships present but disabled until you do this
- see `automation/n8n/README.md` for exactly what is and isn't verified. Until then, real email reaches the same
pipeline through **Import an email** in the app (paste the text) or the generic ingest webhook.

## n8n setup

Workflows are generated from source, not hand-edited JSON: `node automation/n8n/build.js` writes
`automation/n8n/workflows/*.json` (committed, so a diff shows exactly what changed); `docker compose up` imports
and activates them automatically. Full detail, including what each workflow does and the redaction/idempotency
rules: **`automation/n8n/README.md`**.

## Email notification setup

By default, outgoing email is caught by the bundled Mailpit and never leaves your machine - the app says so
plainly (a banner, and `/api/system/status`'s `email_delivery` field) rather than reporting a false success. For a
real account (Gmail shown; any SMTP provider works the same way), set in `.env`:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_SECURE=true
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<a Google App Password - Account > Security > 2-Step Verification > App passwords>
NOTIFY_FROM_EMAIL=you@gmail.com
```
then `docker compose up -d api && ./scripts/n8n-reload.sh`.

## Database setup

PostgreSQL, managed by Alembic migrations (`apps/api/alembic`), applied automatically on API start-up. Locally,
Docker Compose provisions it; in production, point `DATABASE_URL` at any Postgres 14+ instance (a free one is
covered in the deployment guide below). Integrity is enforced in the database itself: enum `CHECK` constraints,
an idempotency ledger (unique `(user, source_type, external_id)`), an anti-spam constraint on notifications, and an
append-only trigger on the audit log.

## Deployment

A concrete, zero-cost path (Vercel + Render + Neon), a ready-to-use `render.yaml` Blueprint, and the exact
click-by-click steps: **[docs/deployment.md](docs/deployment.md)**.

Running deployment: frontend on Vercel at <https://commitmentos.vercel.app>, API on Render at
<https://commitmentos-api.onrender.com>, n8n on Render, Postgres on Neon. The Render services sleep when idle, so the
first request after a quiet period is slow, and n8n needs several minutes to become ready after waking (see
[Known limitations](docs/deployment.md#known-limitations)).

## Production configuration

At minimum, set for a production deployment (see `.env.example` for the full list): `APP_ENV=production`,
`COOKIE_SECURE=true`, a real `JWT_SECRET`, `CORS_ORIGINS` restricted to your real frontend origin (never `*`),
`ALLOW_REGISTRATION=false` once your own account exists, and real SMTP credentials if you want email delivered.

## Security

- Passwords hashed (bcrypt via passlib); sessions are short-lived signed JWTs in an httpOnly, SameSite cookie.
- Every object lookup is scoped to the authenticated user; cross-user access returns 404, not 403 (no existence
  leak). Shared, system-wide data (automation runs) is sanitized before it reaches another user.
- Two independent shared secrets authenticate the two directions between n8n and the API; neither can call the
  other's endpoints, and neither can call user-facing routes.
- Every n8n → API handler is idempotent; every state-changing claim (approvals, notifications) is atomic
  (`FOR UPDATE SKIP LOCKED`), so a duplicate webhook or a race can never double-send or double-execute.
- Rate limiting on login, registration and the general API surface.
- Secrets never appear in logs, error text, or workflow JSON - only in `.env` and, encrypted, inside n8n.

## Testing

765+ backend tests (real Postgres, mutation-checked to prove they can fail), 68 n8n workflow-code unit tests, 33
end-to-end tests against a live, fully isolated stack (real n8n executions, real SMTP into Mailpit), and frontend
unit tests (time/timezone/DST handling, form logic, contrast, safe redirects). Exact commands above, under
*Local development*.

## Known limitations

- **Gmail Trigger, Google Calendar and Telegram delivery are NOT VERIFIED** in this environment - they need
  credentials only you can create (see *Google OAuth / Gmail integration* above). Everything upstream and
  downstream of them is exercised through the webhook entry points and the documented failure paths.
- A follow-up email is sent from the system account with you as `Reply-To`, not from your own mailbox (no
  threading headers - n8n's email node doesn't expose them).
- The rate limiter and the (test-only) virtual clock are per-process; the API is designed to run as one process.
- On the free deployment path, reminders depend on n8n staying awake, which a free, sleeping container does not
  guarantee - see *Known limitations* in `docs/deployment.md` for the honest detail.
- No paid hosted language model has been evaluated; see `docs/eval/README.md` for what was measured, and against
  which models.

## Live application

Not yet deployed. Follow `docs/deployment.md` to put it on the internet for free; this line will carry the real
URL once it is.
