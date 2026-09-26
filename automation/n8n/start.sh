#!/bin/sh
# Container entrypoint (see Dockerfile for why): start n8n immediately so the port opens fast, then run the
# import in the background once n8n answers its own local health check.
set -eu

tini -- /docker-entrypoint.sh &
N8N_PID=$!
trap 'kill -TERM "$N8N_PID" 2>/dev/null; wait "$N8N_PID" 2>/dev/null' TERM INT

echo "[start] waiting for n8n to open its port before importing..."
until wget -q -O /dev/null "http://127.0.0.1:${N8N_PORT:-5678}/healthz" 2>/dev/null; do
  # n8n died before ever coming up (a real startup failure, not a slow one) - stop waiting on it forever
  kill -0 "$N8N_PID" 2>/dev/null || { echo "[start] n8n exited before starting; not running import"; wait "$N8N_PID"; exit $?; }
  sleep 1
done
echo "[start] n8n is up; importing in the background"
/automation/import.sh &

wait "$N8N_PID"
