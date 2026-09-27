# A single deployable n8n image for platforms that run one process per service (no separate init container,
# unlike the local Docker Compose stack, which runs `import.sh` as its own one-shot `n8n-import` service).
#
# See start.sh: importing must still finish before n8n's real process starts (activating a workflow does not
# reach an already-running n8n - only its own next boot reads the change), and that import - credentials, then
# the freshly-built workflows, then activating each one, each activation its own n8n CLI process - can take
# several minutes on a free-tier CPU. A tiny placeholder holds the port open honestly (this process is alive; it
# will become n8n very shortly) so a platform's own port/health detection during the deploy (Render included)
# does not give up waiting, without anything running concurrently with n8n itself. import.sh is safe to re-run
# (n8n's import commands upsert by id), so this is also correct on every restart.
FROM n8nio/n8n:2.40.6
USER root
COPY . /automation
RUN chmod +x /automation/import.sh /automation/start.sh && chown -R node:node /automation
USER node
# tini is PID 1 here (not nested inside start.sh) so it can reap n8n's children properly.
ENTRYPOINT ["tini", "-g", "--", "/bin/sh", "/automation/start.sh"]
