'use strict';
const { Workflow, webhookTrigger, subWorkflowTrigger, api, report, ifTrue, code, runSubWorkflow } = require('../lib/wf');
const { codeNode, markStart, runStartedBody, runFinishedBody } = require('./common');
const registry = require('./registry');

const KEY = 'completion-detection';

// "Does this message show that something I am tracking is done?" The API finds the few plausible candidates (no
// candidates -> no LLM call), the LLM classifies, the API validates. What comes back is only ever a PROPOSAL to
// mark the commitment done; a person approves it, and only then do its reminders stop.
module.exports = function build() {
  const { id, name } = registry[KEY];
  const wf = new Workflow({ id, key: KEY, name, errorWorkflowId: registry['error-handler'].id, description: 'Proposes completing a commitment when a message says it is done.' });

  const called = wf.add(subWorkflowTrigger('Called by another workflow'), [0, 0]);
  const hook = wf.add(webhookTrigger('Check a message (webhook)', 'commitmentos-completion-check'), [0, 2]);
  const start = wf.add(markStart(), [1, 1]);
  wf.link(called, start);
  wf.link(hook, start);
  const unpack = wf.add(code('Unpack message', 'const j = $input.first().json;\nconst body = j.body && j.body.message ? j.body : j;\nreturn [{ json: { user_email: body.user_email, message: body.message } }];'), [2, 1]);

  const check = wf.add(api('Check completion', 'POST', '/api/internal/completion/check', { body: '$json', retries: 3, waitMs: 3000, timeoutMs: 240000 }), [3, 1]);
  const assess = wf.add(codeNode('Assess', 'scans.js', 'return [{ json: assessCompletion($input.first().json) }];'), [4, 1]);
  const asked = wf.add(ifTrue('Was the model asked?', '{{ $json.llm_called }}'), [5, 1]);
  const quiet = wf.add(code('Nothing to compare', 'return [{ json: { idle: true, candidates: 0, n8n_execution_id: String($execution.id) } }];'), [6, 3]);
  wf.chain(start, unpack, check, assess, asked);
  wf.link(asked, quiet, 1); // no candidate commitments: nothing to record

  const matched = wf.add(ifTrue('Something looks done?', '{{ $json.matches > 0 }}'), [6, 0]);
  wf.link(asked, matched, 0);

  // matches: announce the run first (proposals link to it), propose, then WAIT for the person
  const started = wf.add(report('Run started', runStartedBody(KEY, "$('Mark start').first().json._trigger", { status: 'RUNNING' }), { retries: 3, bestEffort: true }), [7, -1]);
  const build = wf.add(codeNode('Build proposals', 'scans.js', "return ($('Check completion').first().json.matches || []).map((m) => ({ json: completionProposal(m, $execution.id) }));"), [8, -1]);
  const propose = wf.add(report('Propose', '$json', { retries: 3 }), [9, -1]);
  const summarise = wf.add(codeNode('Summarise', 'scans.js', 'return [{ json: summariseProposals($input.all().map((i) => i.json)) }];'), [10, -1]);
  const deliver = wf.add(runSubWorkflow('Deliver notifications', registry['notification-dispatcher'].id, { bestEffort: true }), [11, -1]);
  const waiting = wf.add(
    report(
      'Report run (waiting for approval)',
      runFinishedBody(KEY, {
        trigger: "$('Mark start').first().json._trigger",
        status: "$('Summarise').first().json.created > 0 ? 'WAITING' : 'SUCCESS'",
        result: "{ candidates: $('Assess').first().json.candidates, matches: $('Assess').first().json.matches, proposals: $('Summarise').first().json.created }",
      }),
      { retries: 3, bestEffort: true },
    ),
    [12, -1],
  );
  wf.link(matched, started, 0);
  wf.chain(started, build, propose, summarise, deliver, waiting);

  // asked the model, nothing was done: still worth a line in the history (it cost a model call)
  const none = wf.add(
    report(
      'Report run (nothing completed)',
      runFinishedBody(KEY, {
        trigger: "$('Mark start').first().json._trigger",
        result: "{ candidates: $('Assess').first().json.candidates, matches: 0 }",
      }),
      { retries: 3, bestEffort: true },
    ),
    [7, 1],
  );
  wf.link(matched, none, 1);

  const result = wf.add(code('Result', "return [{ json: { ...$('Assess').first().json, n8n_execution_id: String($execution.id) } }];"), [13, 0]);
  wf.link(waiting, result);
  wf.link(none, result);
  return wf;
};
