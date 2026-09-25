'use strict';
// Calendar Sync and the Follow-up Assistant share one shape: scan (deterministic, in the API) -> propose -> WAIT for a person.
// Nothing is created or sent by these workflows; a proposal is an approval request the user decides on.
const { Workflow, scheduleTrigger, webhookTrigger, api, report, ifTrue, code, runSubWorkflow } = require('../lib/wf');
const { codeNode, markStart, runStartedBody, runFinishedBody } = require('./common');
const registry = require('./registry');

module.exports = function scanWorkflow({ key, minutes, webhookPath, scanPath, description, scheduleName }) {
  const { id, name } = registry[key];
  const wf = new Workflow({ id, key, name, errorWorkflowId: registry['error-handler'].id, description });

  const every = wf.add(scheduleTrigger(scheduleName, { minutes }), [0, 0]);
  const hook = wf.add(webhookTrigger('Run now (webhook)', webhookPath), [0, 2]);
  const start = wf.add(markStart(), [1, 1]);
  wf.link(every, start);
  wf.link(hook, start);

  const scan = wf.add(api('Scan', 'POST', scanPath, { body: '{ limit: 25 }', retries: 3, waitMs: 3000 }), [2, 1]);
  const found = wf.add(ifTrue('Anything to propose?', '{{ $json.count > 0 }}'), [3, 1]);
  const idle = wf.add(code('Nothing to propose', 'return [{ json: { idle: true, proposals: 0, n8n_execution_id: String($execution.id) } }];'), [4, 3]);
  wf.chain(start, scan, found);
  wf.link(found, idle, 1);

  // the run is announced BEFORE proposing, so the API can link each proposal to this run
  const started = wf.add(report('Run started', runStartedBody(key, "$('Mark start').first().json._trigger"), { retries: 3, bestEffort: true }), [4, 0]);
  const split = wf.add(codeNode('Split proposals', 'scans.js', "return scanItems($('Scan').first().json).map((json) => ({ json, pairedItem: { item: 0 } }));"), [5, 0]);
  const build = wf.add(codeNode('Build proposals', 'scans.js', 'return $input.all().map((i) => ({ json: proposalEvent(i.json, $execution.id) }));'), [6, 0]);
  const propose = wf.add(report('Propose', '$json', { retries: 3 }), [7, 0]);
  const summarise = wf.add(codeNode('Summarise', 'scans.js', 'return [{ json: summariseProposals($input.all().map((i) => i.json)) }];'), [8, 0]);
  wf.link(found, started, 0);
  wf.chain(started, split, build, propose, summarise);

  // "approval needed" emails were queued by the API: deliver them now instead of waiting for the next minute
  const deliver = wf.add(runSubWorkflow('Deliver notifications', registry['notification-dispatcher'].id, { bestEffort: true }), [9, 0]);

  // the run now WAITS for the human: it resolves when every proposal it made has been decided
  const finish = wf.add(
    report(
      'Report run (waiting for approval)',
      runFinishedBody(key, {
        trigger: "$('Mark start').first().json._trigger",
        status: "$('Summarise').first().json.created > 0 ? 'WAITING' : 'SUCCESS'",
        result: "$('Summarise').first().json",
      }),
      { retries: 3, bestEffort: true },
    ),
    [10, 0],
  );
  const result = wf.add(code('Result', "return [{ json: { ...$('Summarise').first().json, n8n_execution_id: String($execution.id) } }];"), [11, 0]);
  wf.chain(summarise, deliver, finish, result);
  return wf;
};
