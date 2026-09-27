// A tiny reverse proxy that owns the container's external port for its ENTIRE life (see start.sh for why): a
// platform's port/health detection - and n8n's own restart to pick up freshly-activated workflows, which is
// unavoidable (activating a workflow only writes the database; the already-running process never sees it) -
// would otherwise see the port go dark for the several seconds n8n itself takes to rebind on any restart.
//
// n8n binds an INTERNAL port instead (see start.sh); this process holds the real external port and forwards to
// it. Traffic here is small JSON (health checks, and our own API's webhook calls), never a large upload or a
// long-lived stream, so buffering each request and response whole is simpler than piping and is not a
// meaningful cost.
//
// /healthz is answered by this process directly, never forwarded to n8n: the platform's health check only needs
// to know the container itself is alive and listening, which is true the moment this process starts - forwarding
// it instead would tie a 5-second platform deadline to n8n's own response latency (event loop contention during
// its internal task broker / DB work can occasionally push even a healthy n8n past 5 seconds), which caused a
// live restart of an otherwise-fine, fully-booted instance. Every other path still forwards through to n8n
// unchanged, since real callers (our own API's webhook) need n8n's real state, not a synthetic one.
'use strict';

const http = require('node:http');

const externalPort = Number(process.argv[2]);
const internalPort = Number(process.argv[3]);
if (!externalPort || !internalPort) {
  console.error('[proxy] usage: proxy.js <externalPort> <internalPort>');
  process.exit(1);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  if (req.url === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"status":"alive"}');
    return;
  }

  let body;
  try {
    body = await readBody(req);
  } catch {
    res.writeHead(400);
    res.end('bad request');
    return;
  }

  const upstream = http.request(
    { host: '127.0.0.1', port: internalPort, path: req.url, method: req.method, headers: req.headers, timeout: 60_000 },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers);
      upstreamRes.pipe(res);
    },
  );
  upstream.on('timeout', () => upstream.destroy());
  upstream.on('error', () => {
    if (res.headersSent) return;
    // n8n is not reachable yet (still importing) or between its own restarts - never claim more than that.
    res.writeHead(503);
    res.end('still starting');
  });
  upstream.end(body);
});

server.listen(externalPort, '0.0.0.0', () => {
  console.log(`[proxy] listening on ${externalPort}, forwarding to 127.0.0.1:${internalPort}`);
});
