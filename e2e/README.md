# End-to-end tests (live stack)

These tests drive a *running* Docker stack: n8n workflows -> API -> PostgreSQL -> Mailpit. They prove that n8n is really
orchestrating (executions exist and succeed, retries happen, failures surface), not that JSON files exist.

    apps/api/.venv/bin/python -m pytest e2e -q

**They never touch the stack that holds real data.** The session builds its own compose project, `commitmentos-e2e`, from an
empty database volume, with its own ports (18000 API, 15678 n8n, 15433 Postgres, 18025 Mailpit), its own randomly generated
secrets (`.env.e2e`, git-ignored, created on first run) and no language-model key. It starts a scripted stand-in for the model on
the host, so results are deterministic and no quota is spent, and it turns on the test clock (`TEST_CLOCK=true`), a virtual clock
that lets a two-day reminder ladder play out in seconds. The clock does not exist on the real stack, and the suite refuses to
run if the API it finds does not have it, or if `.env.e2e` shares a port, secret or model key with the real `.env`.

When the run ends the stack is removed together with its volumes. `E2E_KEEP_STACK=1` leaves it running for inspection
(`docker compose -p commitmentos-e2e --env-file .env.e2e ps`); remove it with `... down --volumes`. The first run builds the API
image and boots n8n, so it takes a few minutes. Every message and account the suite creates is invented for the test.
