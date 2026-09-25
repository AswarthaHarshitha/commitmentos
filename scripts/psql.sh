#!/usr/bin/env bash
# Run psql inside the compose postgres container.   ./scripts/psql.sh -c "select 1"
# Target another database with PGDB=commitmentos_test ./scripts/psql.sh ...
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
if [ -t 0 ]; then tty_flag=""; else tty_flag="-T"; fi
# shellcheck disable=SC2086
exec docker compose exec $tty_flag postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "${PGDB:-$POSTGRES_DB}" "$@"' sh "$@"
