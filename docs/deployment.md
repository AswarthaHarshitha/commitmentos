# Deployment (free tier)

A concrete, ordered walkthrough for putting CommitmentOS on the public internet at zero cost, using services that
genuinely have a free tier today (checked at the time this was written, not assumed from memory):

| Piece | Provider | Why |
|---|---|---|
| Frontend | [Vercel](https://vercel.com) (Hobby plan) | Free for personal projects; builds Next.js natively, no Dockerfile needed |
| API | [Render](https://render.com) (Free web service) | Free Docker web services; 750 shared instance-hours/workspace/month |
| n8n | [Render](https://render.com) (Free web service) | Same account, same Docker-based deploy |
| Database | [Neon](https://neon.com) (Free plan) | Free Postgres that does not expire (Render's own free Postgres is deleted after 30 days) |

**Read [Known limitations](#known-limitations) before you rely on this for anything real.** The free tiers above
sleep when idle, and n8n's own scheduled triggers (the deadline monitor, the notification dispatcher) cannot fire
while it is asleep - this is stated plainly rather than glossed over.

## 1. Neon: the database

1. Create a free account at [neon.com](https://neon.com) (no card) and a new project.
2. Neon gives you one Postgres instance with one database already created; note its connection string, e.g.
   `postgresql://user:AbC123@ep-cool-name-12345.region.aws.neon.tech/neondb?sslmode=require`.
3. Rename that first use to be the app's database, and add a second database for n8n. In Neon's SQL editor:
   ```sql
   CREATE DATABASE n8n;
   ```
   You now have two connection strings that differ only in the database name at the end (`.../commitmentos_db...`
   vs `.../n8n...` - or whatever you named the first one).
4. For the API's `DATABASE_URL`, change the scheme from `postgresql://` to `postgresql+psycopg://` (the API uses
   psycopg3) and keep `?sslmode=require`. For n8n, you'll split the same connection string into its pieces (host,
   database, user, password) in step 3 below - n8n takes discrete fields, not one URL.

## 2. Render: the API

1. Create a free account at [render.com](https://render.com) (GitHub login) and push this repository to GitHub
   first (see the main README's "Deployment" section for the exact commands).
2. **New > Blueprint**, connect the GitHub repo. Render reads `render.yaml` at the repo root and proposes two
   services: `commitmentos-api` and `commitmentos-n8n`. Approve the blueprint.
3. Render prompts you for every environment variable marked `sync: false` in `render.yaml`. For `commitmentos-api`,
   fill in at least:
   - `DATABASE_URL` - Neon's connection string for the app's database, with `postgresql+psycopg://` and
     `?sslmode=require`.
   - `N8N_INBOUND_SECRET`, `N8N_OUTBOUND_SECRET` - generate two random values yourself (e.g.
     `openssl rand -hex 24` on your own machine), and remember them - you'll paste the *same two values* into the
     n8n service in the next step.
   - `N8N_BASE_URL` - leave a placeholder for now (e.g. `https://commitmentos-n8n.onrender.com`, adjusted once you
     know the real n8n service URL Render assigns).
   - `PUBLIC_WEB_URL`, `CORS_ORIGINS` - leave a placeholder (e.g. `https://commitmentos.vercel.app`); you'll come
     back and set the real Vercel URL in step 4.
   - `GEMINI_API_KEY` - a [Google AI Studio](https://aistudio.google.com/apikey) key, if you have one; otherwise
     leave it empty and set `LLM_PROVIDER=none` (extraction is then recorded as a visible failure instead of
     silently guessing - see `docs/architecture.md`).
   - `NOTIFY_FROM_EMAIL`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` - a real account to send from (see step 6),
     or leave empty for now.
4. Deploy. Note the resulting API URL, e.g. `https://commitmentos-api.onrender.com`. Check
   `https://commitmentos-api.onrender.com/api/health` returns `{"status":"ok","db":"ok",...}`.

## 3. Render: n8n

Still inside the same Blueprint, fill in `commitmentos-n8n`'s `sync: false` fields:

- `COMMITMENTOS_API_URL` - the real API URL from step 2 (e.g. `https://commitmentos-api.onrender.com`). Workflows
  are rebuilt from source against this URL every time the container starts (see `automation/n8n/import.sh`) - it is
  an ordinary environment variable, not something baked in at build time.
- `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_DATABASE`, `DB_POSTGRESDB_USER`, `DB_POSTGRESDB_PASSWORD` - the pieces of
  Neon's connection string for the **`n8n` database** (the second one you created in step 1), not the app's
  database. `DB_POSTGRESDB_HOST` is the host only (`ep-....neon.tech`), no scheme, no path.
- `N8N_INBOUND_SECRET`, `N8N_OUTBOUND_SECRET` - the *same two values* you generated for the API in step 2.
- `N8N_HOST` - the host only (`commitmentos-n8n.onrender.com`, no scheme).
- `N8N_WEBHOOK_URL` - the full URL with a trailing slash (`https://commitmentos-n8n.onrender.com/`).
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` - the same values as the API's, if you set them (n8n itself sends the
  email; the API only ever reports where it went).

Deploy. Check `https://commitmentos-n8n.onrender.com/healthz` returns `200`. The first boot imports credentials
and eight workflows and activates each one - watch the logs for `[import] done`, then `n8n ready on ::, port 5680`
(n8n listens on an internal port; a small proxy in front of it owns the public one - see Known limitations).

**Claim the n8n owner account straight away.** A fresh n8n serves its "set up owner account" page to whoever opens the
URL first, and the service is public. Open `https://commitmentos-n8n.onrender.com` yourself and create the owner
(or check `/rest/settings`: `userManagement.showSetupOnFirstLoad` must be `false`). Until you do, a stranger could
become the owner and run workflows that use your stored SMTP credential.

## 4. Vercel: the frontend

1. Create a free account at [vercel.com](https://vercel.com) (GitHub login) and let it access the repository.
2. **Add New > Project**, import the same GitHub repo. Vercel auto-detects Next.js; set:
   - **Root Directory**: `apps/web`
   - **Environment Variable**: `API_URL` = the Render API URL from step 2 (e.g.
     `https://commitmentos-api.onrender.com`) - it configures the `/api/*` proxy (`apps/web/next.config.ts`), so the
     browser only ever talks to one origin and the session cookie stays first-party.
3. Deploy. Note the resulting URL, e.g. `https://commitmentos.vercel.app`. The project is linked to the repository, so
   every push to `main` redeploys the frontend.

## 5. Close the loop: point the API back at the real frontend URL

Go back to the `commitmentos-api` service on Render and set the real values now that you have them:

- `PUBLIC_WEB_URL` and `CORS_ORIGINS` = the Vercel URL from step 4 (e.g. `https://commitmentos.vercel.app`).
- `N8N_BASE_URL` = the n8n URL from step 3.

Manually redeploy `commitmentos-api` for the change to take effect.

## 6. Real SMTP

Without this, email is caught by n8n's default configuration and never reaches anyone - the API reports this honestly
(`email_delivery: "local_test_inbox"` from `/api/system/status`, and a banner in the app) rather than claiming success.
Set these on **both** Render services (they must match), and on the API only `NOTIFY_FROM_EMAIL`:

```
SMTP_HOST=<provider host>
SMTP_PORT=<port>
SMTP_SECURE=<true for implicit TLS on 465, false for STARTTLS>
SMTP_USER=<smtp username>
SMTP_PASSWORD=<smtp password or app password - never the account password>
```

**Render's free tier blocks outbound SMTP on ports 25, 465 and 587**
([Render docs](https://render.com/docs/free)). Gmail's SMTP servers only offer those ports, so Gmail SMTP works locally
but **cannot** work from a free Render service. This was hit on the live deployment: an approved follow-up was claimed
by n8n, the send timed out (`Connection timeout`), and the API recorded the failure and queued a retry - it never
reported the message as sent. Options that work within the constraint:

- an SMTP provider that also listens on another port, set as `SMTP_PORT` (Brevo documents port 2525 with STARTTLS, so
  `SMTP_SECURE=false`; this exact setup has not been tested here), or
- a paid Render instance type, which is not subject to the block, or
- running n8n somewhere without the restriction.

For Gmail as the sender on a platform that allows SMTP: turn on 2-Step Verification, create an **App Password**
(Google Account -> Security -> 2-Step Verification -> App passwords), and use `smtp.gmail.com`, port `465`,
`SMTP_SECURE=true`.

## 7. Verify the live deployment

```
curl https://commitmentos-api.onrender.com/api/health
curl https://commitmentos-n8n.onrender.com/healthz
curl -s https://commitmentos-n8n.onrender.com/rest/settings   # userManagement.showSetupOnFirstLoad must be false
```
Then, in a browser: open the Vercel URL, register an account, sign in, add a commitment by hand, and use
**Import an email** (top bar) to paste a real email - this exercises the real pipeline (n8n -> API -> language
model -> validation -> Postgres -> the commitment in the UI) without needing Gmail access. `/healthz` only says the
container is alive: the n8n webhook answers `503 still starting` until the post-wake import finishes, so an import made
too early is reported as failed and can simply be repeated.

## Known limitations

- **Gmail is NOT VERIFIED here.** There is no custom Google OAuth flow in this codebase - Gmail access is n8n's
  own built-in *Gmail Trigger* node, which needs its own Google Cloud OAuth2 credential, created and authorized
  **inside n8n's UI** (Settings -> Credentials -> New -> Gmail Trigger, which opens Google's consent screen). That
  requires your own Google Cloud project and a browser click-through only you can do; the node is present and
  disabled until you do this. Without it, real mail still reaches CommitmentOS through **Import an email** (paste)
  and through the generic ingest webhook - both go through the identical detection pipeline.
- **Reminders depend on n8n staying awake, and the free tier does not guarantee that.** Render's free web services
  spin down after 15 minutes of no HTTP traffic. n8n's own internal schedule triggers (the deadline monitor, the
  notification dispatcher) are just cron jobs *inside that process* - they do not fire while it is asleep, and
  nothing wakes it back up except an inbound HTTP request. This is not "production-grade reminder reliability";
  it is what a free, sleeping container can honestly offer. The simplest free-compatible mitigation is an external
  uptime pinger (e.g. a free account on a service like UptimeRobot hitting `/healthz` every few minutes) to keep
  it warm - which itself trades against the shared 750-hour/workspace budget across both Render services.
- **Render's 750 free hours are shared across every free service in the workspace.** Running both
  `commitmentos-api` and `commitmentos-n8n` continuously all month needs roughly double that budget between them;
  in practice at least one will spend part of the month asleep.
- **Neon's free compute suspends after 5 minutes of inactivity** and resumes in a few hundred milliseconds on the
  next query - a brief, usually unnoticeable delay, unlike Render's ~1-minute cold start.
- **Waking `commitmentos-n8n` from a cold start takes several minutes, not seconds.** Activating a workflow only
  writes the database - it does not reach n8n's already-running process - so n8n has to import and activate all
  eight workflows and then start completely fresh before any of them really work; each activation is its own n8n
  CLI process, and that is slow (often 5-10+ minutes end to end) on a free, shared CPU. A tiny proxy
  (`automation/n8n/proxy.js`) holds the external port the entire time so the platform's own port and health checks
  see the service as alive throughout, rather than timing out and failing the deploy outright (what an earlier
  version of this image did). The proxy answers `/healthz` itself, immediately, and never forwards it to n8n -
  an earlier version forwarded `/healthz` through to n8n once n8n was reachable, which meant a momentary stall in
  n8n's own event loop (seen live, alongside overlapping DB queries during startup) could push a single health
  check past the platform's 5-second deadline and get an otherwise-healthy instance restarted. Verified live: with
  the direct-answer proxy, a deploy ran for 15+ minutes without a single restart, including through a real,
  logged database connection timeout that n8n recovered from on its own - the platform's health check never saw
  it, because it no longer depends on n8n's own responsiveness. This is a real, durable fix, not just a reduction
  in odds; the remaining cost is purely the several-minute wait itself, which a paid tier with a dedicated CPU
  share would shorten but which this fix does not attempt to shorten.
- **Email from n8n does not work on Render's free tier** (outbound SMTP ports are blocked - see step 6). Detection,
  reminders inside the app, approvals and the audit trail work; delivering a follow-up or a reminder by email needs one
  of the options in step 6.
- **`ALLOW_REGISTRATION` is `true` by default** so the first account can be created; set it to `false` on
  `commitmentos-api` once your own account exists, otherwise `/register` is a public sign-up page.
- **Telegram delivery** needs its own bot token (`TELEGRAM_BOT_TOKEN`), not covered above; the code path exists and
  fails safe (never reported as sent) without one.
