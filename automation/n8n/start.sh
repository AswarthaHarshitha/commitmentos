#!/bin/sh
# Runs as tini's direct child (tini itself is PID 1 - see the Dockerfile's ENTRYPOINT).
#
# n8n does not pick up a workflow another process activated while it is already running ("Please restart n8n
# for changes to take effect" is true, not boilerplate - publish:workflow only writes the database, it does not
# reach the already-running process), so importing must finish, and n8n must then start fresh, before its
# workflows are really active - and a full import (credentials, then the freshly-built workflows, then
# activating each one, each activation its own n8n CLI process) can take several minutes on a free-tier CPU.
#
# n8n's own boot (after the import above finishes) takes several seconds too (module load, migrations), during
# which nothing listens on its port - and a platform that starts a deploy by watching for an open port (Render
# included), or that restarts a container whose health check fails even once, cannot tell that gap apart from the
# service being down. So a small proxy (proxy.js) holds the external port for the container's ENTIRE life, from
# before the import even starts; n8n binds a different, internal port instead (below), and the proxy answers
# honestly (still starting) until n8n is reachable there - so the external port is never, even briefly, unheld.
set -eu

EXTERNAL_PORT="${PORT:-${N8N_PORT:-5678}}"
INTERNAL_PORT=5680
export N8N_PORT="$INTERNAL_PORT"

node /automation/proxy.js "$EXTERNAL_PORT" "$INTERNAL_PORT" &
PROXY_PID=$!
trap 'kill -TERM "$PROXY_PID" 2>/dev/null' TERM INT

echo "[start] importing"
/automation/import.sh

echo "[start] import done; starting n8n on the internal port"
exec /docker-entrypoint.sh
