'use strict';
// Building blocks shared by the workflow definitions.
const { code } = require('../lib/wf');
const { codeSource } = require('../lib/load-code');

/** a Code node = a tested module + a few lines of n8n glue (the only untested part; it is exercised by the live-stack tests) */
const codeNode = (name, file, glue, opts = {}) => code(name, `${codeSource(file)}\n\n${glue}`, opts);

/**
 * Records when the run began (for the run duration) and how it was triggered, passing every item through unchanged
 * apart from `_t0` / `_trigger`. Webhook items carry headers+body, schedule items a timestamp; anything else is a
 * call from another workflow.
 */
const markStart = (name = 'Mark start') =>
  code(
    name,
    `const first = $input.first()?.json ?? {};
const trigger = 'headers' in first && 'body' in first ? 'WEBHOOK' : 'timestamp' in first ? 'SCHEDULE' : 'SUBWORKFLOW';
return $input.all().map((i) => ({ json: { ...i.json, _t0: Date.now(), _trigger: trigger } }));`,
    { notes: 'Remembers when this run began and what started it (for the run duration and trigger).' },
  );

/**
 * run.started event body (as a JS expression for `report`). Workflows that propose something for a human to approve
 * call this BEFORE proposing, so the API can link the proposals to this run.
 */
const runStartedBody = (key, trigger, { status = 'RUNNING', userEmail = 'null', correlation = 'null' } = {}) =>
  `{ event: 'run.started', workflow_key: '${key}', n8n_execution_id: String($execution.id), n8n_workflow_id: $workflow.id, workflow_name: $workflow.name, trigger: ${trigger}, status: '${status}', user_email: ${userEmail}, correlation_id: ${correlation} }`;

/** run.finished event body; `extra` is an object-literal fragment such as "obligation_id: x" */
const runFinishedBody = (key, { trigger = 'null', status = "'SUCCESS'", result = '{}', error = 'null', start = "$('Mark start').first().json._t0", extra = '' } = {}) =>
  `{ event: 'run.finished', workflow_key: '${key}', n8n_execution_id: String($execution.id), n8n_workflow_id: $workflow.id, workflow_name: $workflow.name, trigger: ${trigger}, status: ${status}, result: ${result}, error: ${error}, duration_ms: Math.max(0, Date.now() - ${start})${extra ? ', ' + extra : ''} }`;

module.exports = { codeNode, markStart, runStartedBody, runFinishedBody };
