# End-to-end tests (live stack)

These tests drive the *running* Docker stack: n8n workflows -> API -> PostgreSQL -> Mailpit. They prove that n8n is
really orchestrating (executions exist and succeed, retries happen, failures surface), not that JSON files exist.

    docker compose up -d
    apps/api/.venv/bin/python -m pytest e2e -q

The session fixture starts a scripted stand-in for the LLM on the host and restarts the `api` container to talk to it
(`LLM_PROVIDER=openai_compat`, `DEMO_MODE=true`), so results are deterministic and no LLM quota is spent. It restores the
container's normal configuration afterwards. Everything sent is synthetic; secrets are read from `.env` and never printed.
