#!/bin/sh
# Runs inside the n8n image (busybox sh) as a one-shot job before n8n starts.
#   0) rebuild workflows/*.json into a writable path, against COMMITMENTOS_API_URL from THIS environment
#      (/automation is read-only in local Compose; always regenerating removes any risk of it drifting
#      from source, and is how a deployed image points its workflows at its own real API URL at runtime -
#      an ordinary env var, rather than something baked in at build time)
#   1) credentials from env -> n8n (encrypted with N8N_ENCRYPTION_KEY)
#   2) the freshly-built workflows -> n8n
#   3) publish (activate) every workflow, so triggers are live the moment n8n starts
# n8n's CLI cannot activate on import outside queue mode, hence the explicit publish step.
set -eu

echo "[import] building workflows (COMMITMENTOS_API_URL=${COMMITMENTOS_API_URL:-http://api:8000})"
rm -rf /tmp/workflows
node /automation/build.js --out /tmp/workflows

echo "[import] building credentials from environment"
node /automation/make-credentials.js > /tmp/credentials.json
n8n import:credentials --input=/tmp/credentials.json
rm -f /tmp/credentials.json

echo "[import] importing workflows"
n8n import:workflow --separate --input=/tmp/workflows

for file in /tmp/workflows/*.json; do
  id="$(node -p "require('$file').id")"
  echo "[import] publishing $id ($(basename "$file"))"
  n8n publish:workflow --id="$id"
done

echo "[import] done"
