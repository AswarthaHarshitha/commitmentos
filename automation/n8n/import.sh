#!/bin/sh
# Runs inside the n8n image (busybox sh) as a one-shot job before n8n starts.
#   1) credentials from env -> n8n (encrypted with N8N_ENCRYPTION_KEY)
#   2) workflows from /automation/workflows -> n8n
#   3) publish (activate) every workflow, so triggers are live the moment n8n starts
# n8n's CLI cannot activate on import outside queue mode, hence the explicit publish step.
set -eu

echo "[import] building credentials from environment"
node /automation/make-credentials.js > /tmp/credentials.json
n8n import:credentials --input=/tmp/credentials.json
rm -f /tmp/credentials.json

echo "[import] importing workflows"
n8n import:workflow --separate --input=/automation/workflows

for file in /automation/workflows/*.json; do
  id="$(node -p "require('$file').id")"
  echo "[import] publishing $id ($(basename "$file"))"
  n8n publish:workflow --id="$id"
done

echo "[import] done"
