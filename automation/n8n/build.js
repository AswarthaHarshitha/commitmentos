#!/usr/bin/env node
'use strict';
// Generates the n8n workflow JSON files.
//   node build.js                 write workflows/*.json
//   node build.js --check         fail if the committed files differ from what would be generated (used by CI / tests)
//   node build.js --out <dir>     write somewhere else
const fs = require('node:fs');
const path = require('node:path');
const registry = require('./definitions/registry');

const definitions = {
  'error-handler': require('./definitions/error-handler'),
  'notification-dispatcher': require('./definitions/notification-dispatcher'),
};
for (const optional of ['incoming-detection', 'deadline-monitor', 'approved-actions', 'calendar-sync', 'follow-up-assistant', 'completion-detection']) {
  const file = path.join(__dirname, 'definitions', `${optional}.js`);
  if (fs.existsSync(file)) definitions[optional] = require(file);
}

const args = process.argv.slice(2);
const check = args.includes('--check');
const outIndex = args.indexOf('--out');
const outDir = outIndex >= 0 ? path.resolve(args[outIndex + 1]) : path.join(__dirname, 'workflows');

function render(key) {
  const wf = definitions[key]();
  const nodeNames = new Set(wf.nodes.map((n) => n.name));
  for (const [from, conn] of Object.entries(wf.connections)) {
    if (!nodeNames.has(from)) throw new Error(`${key}: connection from unknown node "${from}"`);
    for (const branch of conn.main) for (const edge of branch) if (!nodeNames.has(edge.node)) throw new Error(`${key}: connection to unknown node "${edge.node}"`);
  }
  return `${JSON.stringify(wf, null, 2)}\n`;
}

let stale = 0;
fs.mkdirSync(outDir, { recursive: true });
for (const key of Object.keys(definitions)) {
  const target = path.join(outDir, `${key}.json`);
  const text = render(key);
  if (check) {
    const current = fs.existsSync(target) ? fs.readFileSync(target, 'utf8') : null;
    if (current !== text) {
      console.error(`STALE: ${path.relative(process.cwd(), target)} does not match its definition - run "node automation/n8n/build.js"`);
      stale += 1;
    }
  } else {
    fs.writeFileSync(target, text);
    console.log(`wrote ${path.relative(process.cwd(), target)} (${registry[key].name})`);
  }
}
process.exit(stale ? 1 : 0);
