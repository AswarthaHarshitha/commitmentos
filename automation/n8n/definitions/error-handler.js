'use strict';
const { Workflow, errorTrigger, report } = require('../lib/wf');
const { codeNode } = require('./common');
const registry = require('./registry');

// The workflow every other workflow names as its error workflow: a failed execution anywhere becomes a visible FAILED run.
module.exports = function build() {
  const { id, name } = registry['error-handler'];
  const wf = new Workflow({ id, key: 'error-handler', name, description: 'Reports any failed workflow execution to the automation history.' });
  const keys = Object.fromEntries(Object.entries(registry).map(([key, v]) => [v.id, key]));

  const trigger = wf.add(errorTrigger('Workflow failed'), [0, 0]);
  const shape = wf.add(codeNode('Shape failure', 'errors.js', `const KEYS = ${JSON.stringify(keys)};\nreturn $input.all().map((i) => ({ json: shapeError(i.json, KEYS) }));`), [1, 0]);
  const send = wf.add(report('Report failed run', '$json', { retries: 4, waitMs: 3000, bestEffort: true }), [2, 0]);
  wf.chain(trigger, shape, send);
  return wf;
};
