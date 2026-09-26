# A single deployable n8n image for platforms that run one process per service (no separate init container,
# unlike the local Docker Compose stack, which runs `import.sh` as its own one-shot `n8n-import` service).
#
# n8n starts FIRST, in the foreground under tini (proper PID 1 / signal handling), so the container's port opens
# within seconds - most PaaS platforms (Render included) detect the listening port during a bounded window early
# in the deploy and give up if nothing is listening yet, and importing eight workflows one at a time (each an n8n
# CLI invocation - a full process start) can take several minutes on a free-tier CPU, comfortably outrunning that
# window if it ran first. import.sh (credentials, then the freshly-built workflows, then activating each one) runs
# concurrently in the background instead, once n8n's own local health check answers; start.sh forwards SIGTERM so
# a redeploy stops cleanly. import.sh is safe to re-run (n8n's import commands upsert by id), so this is also
# correct on every restart, and safe to run alongside n8n's own startup migration (both take the same DB lock).
FROM n8nio/n8n:2.40.6
USER root
COPY . /automation
RUN chmod +x /automation/import.sh /automation/start.sh && chown -R node:node /automation
USER node
# tini is PID 1 here (not nested inside start.sh) so it can reap n8n and import.sh's children properly.
ENTRYPOINT ["tini", "-g", "--", "/bin/sh", "/automation/start.sh"]
