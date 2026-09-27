#!/bin/sh
# Runs as tini's direct child (tini itself is PID 1 - see the Dockerfile's ENTRYPOINT).
#
# n8n does not pick up a workflow another process activated while it is already running ("Please restart n8n
# for changes to take effect" - true, not boilerplate: publish:workflow only writes the database, it does not
# reach the already-running process), so importing must still happen before n8n's real process starts. But a
# platform that starts the deploy by watching for an open port (Render included) can time out waiting for one if
# nothing is listening while that import runs - it takes several minutes for a free, shared CPU to boot n8n's
# CLI eight separate times (once per workflow to publish).
#
# So a tiny placeholder answers the port immediately (a real, honest liveness answer - this process is alive and
# will become n8n very shortly), the import runs undisturbed in the foreground with nothing else competing for
# the CPU or the database, and once it finishes the placeholder steps aside and this script execs into n8n's own
# entrypoint - replacing itself, so tini's direct child simply becomes n8n with no extra process to reap or
# signal separately.
set -eu

node -e '
const server = require("http").createServer((req, res) => {
  if (req.url === "/healthz") { res.writeHead(200); res.end("starting"); return; }
  res.writeHead(503); res.end("still importing");
});
server.listen(process.env.N8N_PORT || 5678, "0.0.0.0");
' &
PLACEHOLDER_PID=$!
trap 'kill -TERM "$PLACEHOLDER_PID" 2>/dev/null; exit 143' TERM INT

echo "[start] placeholder answering the health check; importing..."
/automation/import.sh

kill "$PLACEHOLDER_PID" 2>/dev/null || true
wait "$PLACEHOLDER_PID" 2>/dev/null || true

echo "[start] import done; starting n8n"
exec /docker-entrypoint.sh
