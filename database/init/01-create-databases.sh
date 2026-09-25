#!/bin/sh
# Runs once, on first start of an empty Postgres volume.
#   n8n                -> n8n's own workflow/execution store
#   commitmentos_test  -> throw-away database for the backend test-suite
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE DATABASE n8n;
  CREATE DATABASE commitmentos_test;
EOSQL
