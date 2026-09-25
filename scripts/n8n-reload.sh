#!/usr/bin/env bash
# Development helper: regenerate the workflow JSON, re-import + publish everything, restart n8n.
set -euo pipefail
cd "$(dirname "$0")/.."
node automation/n8n/build.js
docker compose run --rm n8n-import
docker compose up -d --force-recreate n8n
echo "waiting for n8n..."
for _ in $(seq 1 60); do
  if [ "$(docker compose ps --format '{{.Health}}' n8n)" = "healthy" ]; then echo "n8n is healthy"; exit 0; fi
  sleep 2
done
echo "n8n did not become healthy" >&2
exit 1
