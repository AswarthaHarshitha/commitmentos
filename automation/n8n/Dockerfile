# A single deployable n8n image for platforms that run one process per service (no separate init container,
# unlike the local Docker Compose stack, which runs `import.sh` as its own one-shot `n8n-import` service).
#
# At boot, import.sh rebuilds workflows/*.json against COMMITMENTOS_API_URL from the container's own runtime
# environment (an ordinary env var - see import.sh), imports credentials and the freshly-built workflows, then
# publishes (activates) each one, before handing off to n8n's own entrypoint - so triggers are live the moment
# the health check passes. import.sh is safe to re-run (n8n's import commands upsert by id), so this also runs
# correctly on every restart.
FROM n8nio/n8n:2.40.6
USER root
COPY . /automation
RUN chmod +x /automation/import.sh && chown -R node:node /automation
USER node
ENTRYPOINT ["/bin/sh", "-c", "/automation/import.sh && exec tini -- /docker-entrypoint.sh"]
