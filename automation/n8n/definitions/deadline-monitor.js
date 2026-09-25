'use strict';
const { Workflow, scheduleTrigger, webhookTrigger, api, report, ifTrue, code, runSubWorkflow } = require('../lib/wf');
const { codeNode, markStart, runFinishedBody } = require('./common');
const registry = require('./registry');

const KEY = 'deadline-monitor';

// n8n decides WHEN to look; the API decides WHAT is due (24h / 6h / overdue / escalated, snooze, dedupe, quiet hours).
// A tick that did nothing is not recorded, so the automation history stays readable; a failing tick always is.
module.exports = function build() {
  const { id, name } = registry[KEY];
  const wf = new Workflow({ id, key: KEY, name, errorWorkflowId: registry['error-handler'].id, description: 'Evaluates deadlines on a schedule.' });

  const every = wf.add(scheduleTrigger('Every 5 minutes', { minutes: 5 }), [0, 0]);
  const hook = wf.add(webhookTrigger('Run now (webhook)', 'commitmentos-monitor'), [0, 2]);
  const start = wf.add(markStart(), [1, 1]);
  wf.link(every, start);
  wf.link(hook, start);

  const tick = wf.add(api('Evaluate deadlines', 'POST', '/api/internal/monitor/tick', { body: '{}', retries: 3, waitMs: 3000 }), [2, 1]);
  const assess = wf.add(codeNode('Assess tick', 'monitor.js', 'return [{ json: assessTick($input.first().json) }];'), [3, 1]);
  const worked = wf.add(ifTrue('Did anything happen?', '{{ $json.did_work }}'), [4, 1]);
  const idle = wf.add(code('Nothing due', 'return [{ json: { idle: true, n8n_execution_id: String($execution.id) } }];'), [5, 3]);
  wf.chain(start, tick, assess, worked);
  wf.link(worked, idle, 1);

  const finish = wf.add(
    report(
      'Report run',
      runFinishedBody(KEY, {
        trigger: "$('Mark start').first().json._trigger",
        status: '$json.status',
        result: '$json.result',
        error: '$json.error',
      }),
      { retries: 3, bestEffort: true },
    ),
    [5, 0],
  );
  wf.link(worked, finish, 0);
  const deliver = wf.add(ifTrue('Reminders to deliver?', "{{ $('Assess tick').first().json.deliver }}"), [6, 0]);
  const send = wf.add(runSubWorkflow('Deliver notifications', registry['notification-dispatcher'].id, { bestEffort: true }), [7, -1]);
  const result = wf.add(code('Result', "return [{ json: { ...$('Assess tick').first().json, n8n_execution_id: String($execution.id) } }];"), [8, 0]);
  wf.link(finish, deliver);
  wf.link(deliver, send, 0);
  wf.link(deliver, result, 1);
  wf.link(send, result);
  return wf;
};
