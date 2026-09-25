#!/usr/bin/env bash
# Source this to run API tooling (alembic, pytest, uvicorn) on the host against the compose stack:
#     source scripts/dev-env.sh
_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
[ -f "$_root/.env" ] || { echo "no .env - run ./scripts/init-env.sh first" >&2; return 1 2>/dev/null || exit 1; }
set -a
# shellcheck disable=SC1091
source "$_root/.env"
set +a
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_HOST_PORT:-5433}/${POSTGRES_DB}"
export TEST_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_HOST_PORT:-5433}/commitmentos_test"
export N8N_BASE_URL="http://localhost:${N8N_PORT:-5678}"
unset _root
