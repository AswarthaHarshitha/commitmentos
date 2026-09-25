# n8n workflows

n8n is the orchestrator: it decides **when** something runs, **where** data goes, and **what happens when a step fails**
(triggers, schedules, retries, branches, hand-offs between workflows). The API owns every decision that matters
(validation, thresholds, deadline arithmetic, deduplication, authorisation, persistence, audit). The LLM only extracts.

```
 Gmail ─┐                       ┌── /api/internal/messages/check   (seen already? asked BEFORE paying for an LLM call)
 paste ─┴─> Incoming Detection ─┼── /api/internal/extract          (LLM + validation + deterministic deadline)
 webhook                        └── /api/webhooks/n8n              message.extracted  -> obligation / review / candidate / ignore
 every 5 min ──> Deadline Monitor ──> /api/internal/monitor/tick   which reminder is due? (24h, 6h, overdue, escalated)
 every 15 min ─> Calendar Sync ─────> /api/internal/calendar/scan  proposals only
 every hour ───> Follow-up Assistant > /api/internal/followups/scan proposals only (templated drafts)
 reply arrives ─> Completion Detection > /api/internal/completion/check   "does this say it is done?" -> proposal only
 user approves ─> Approved Actions ──> send email / create calendar event, then report the result
 every minute ─> Notification Dispatcher ─> claim queued notifications, send email / Telegram, report each outcome
 any failure ──> Error Handler ──> a FAILED run in the automation history
```

Nothing with an external side effect happens without a person's approval, and the API only ever hands n8n an action after
the *user* moved it to APPROVED (claimed atomically, so a duplicate webhook or a second poll cannot run it twice).

## The workflows

| File | Name (in n8n) | Triggers | What it does |
|---|---|---|---|
| `incoming-detection.json` | Commitment Detection | webhook `commitmentos-ingest`; Gmail Trigger (disabled) | normalise + clean -> duplicate check -> LLM extraction (3 tries) -> validated by the API -> notify -> ask completion detection |
| `deadline-monitor.json` | Deadline Monitor | every 5 min; webhook `commitmentos-monitor` | asks the API which deadlines need action; delivers the resulting reminders; idle ticks are not recorded |
| `notification-dispatcher.json` | Notification Dispatcher | every minute; webhook `commitmentos-dispatch`; called by other workflows | claims queued notifications, sends email (SMTP) / Telegram, reports `sent` / `failed` per notification |
| `approved-actions.json` | Approved Actions | webhook `commitmentos-action` (pushed on approval); every 2 min (catch-up) | claims approved actions atomically, sends the follow-up email or the calendar invite, reports the result |
| `calendar-sync.json` | Calendar Synchronization | every 15 min; webhook `commitmentos-calendar-scan` | proposes calendar events (ask, then create); the run WAITS for the user |
| `follow-up-assistant.json` | Follow-up Assistant | every hour; webhook `commitmentos-followup-scan` | proposes follow-up drafts for overdue / waited-on commitments; the run WAITS for the user |
| `completion-detection.json` | Completion Detection | called by Incoming Detection; webhook `commitmentos-completion-check` | proposes "mark as done" when a message says so (no candidates -> no LLM call); the run WAITS for the user |
| `error-handler.json` | Error Handler | any workflow's error | reports the failed execution (workflow, node, redacted reason) so it is visible to the user |

Every workflow reports to `POST /api/webhooks/n8n` (`run.started` / `run.finished` / `run.failed`), keyed by n8n's own
execution id, so a row in **Automation Activity** can be cross-checked against n8n's execution list. A workflow that ends
"waiting for a human" is reported as `WAITING` and is resolved automatically when every approval it proposed is decided.

## How this directory works

```
definitions/*.js     one module per workflow, built from lib/wf.js (a tiny builder: ids, credentials, retry policy, layout)
code/*.js            the logic inside Code nodes, as plain functions with real unit tests (tests/code.test.js)
lib/                 the builder, the loader shared by build.js and the tests, fixed credential ids
build.js             generates workflows/*.json  (node build.js | node build.js --check)
workflows/*.json     build output (committed), imported by import.sh
import.sh            runs in the n8n image before n8n starts: credentials from env -> workflows -> publish (activate)
make-credentials.js  builds credentials from environment variables, so secrets live only in .env and (encrypted) in n8n
```

Workflow JSON is generated so that every workflow gets the same ids, the same credential wiring, the same retry policy and
the same reporting - and so the Code-node logic is *tested code*, not something hand-pasted into a JSON blob. What the tests
run is exactly what is deployed: `lib/load-code.js` expands the same `// @include` directives that `build.js` inlines.

Regenerate after any change, then reload:

```
node automation/n8n/build.js          # rewrite workflows/*.json
node automation/n8n/build.js --check  # CI: fail if the committed JSON is stale
./scripts/n8n-reload.sh               # re-import, publish, recreate the n8n container
node --test automation/n8n/tests/*.test.js
```

## Design decisions worth knowing

* **Retries are bounded and only where they can help.** Extraction and API calls retry 3 times with a pause. A spent LLM
  quota (`LLM_QUOTA_EXHAUSTED`) is answered `200` with `retryable: false`, so n8n does not hammer it.
* **A gotcha found by the live tests:** n8n treats any output item that has a `json.error` key as a failed item and retries
  the node - even on HTTP 200. The API therefore never puts a top-level `error` key in a reply that n8n consumes
  (`error_message` instead).
* **Idle is silent.** A monitor tick, dispatch or scan that finds nothing is not written to the automation history.
* **No false success.** A disabled or unconnected Google Calendar / Telegram node passes its input straight through in n8n;
  a guard node after each verifies the provider really answered, otherwise the action is reported as *failed*.
* **Failure text is scrubbed** (`code/_common.js`): tokens are redacted, and an HTTP error's echoed request body - which can
  quote an email - is reduced to the status and the API's own error code.
* **`--activeState=fromJson` only works in queue mode**, so `import.sh` publishes each workflow explicitly.
* **n8n stores execution data (message text) for inspection and retry.** Failed runs keep theirs for 72 h; successful runs keep nothing by default (`N8N_SAVE_SUCCESS_EXECUTIONS=none`); for a real
  mailbox set `N8N_SAVE_SUCCESS_EXECUTIONS=none` (`.env.example`).

## What is verified, and what is not

Verified against the running stack by `e2e/` (31 tests: real n8n executions, real SMTP into Mailpit, real API and database):
ingestion end to end; duplicate detection before the LLM call; the same words resolving to different instants in different
timezones; the full 24h / 6h / overdue / escalated ladder driven by the test clock, each rung once, nothing after completion;
retry of a flaky LLM; a permanently unavailable LLM (visible failure, later retry of the same message); malformed and
hallucinated model output; a spent quota; approval, rejection, calendar invite (a real `.ics` attachment), completion
suggested from a reply; a duplicate/racing trigger sending exactly one email; an n8n outage between approval and execution;
an unconnected Telegram channel failing without blocking email.

**NOT VERIFIED - external dependency unavailable:** the Gmail Trigger (needs a Google OAuth credential), the Google Calendar
node (same), and Telegram delivery (needs a bot token). Their nodes are present, disabled or unconnected, with guards that
fail safe. Everything upstream and downstream of them is exercised through the webhook entry points and the failure paths.

Not handled (known limits): threading headers (`In-Reply-To`) are not applied to follow-up emails because n8n's email node
does not expose them; a follow-up is sent from the system address with the user as `Reply-To`, not from the user's own mailbox.
