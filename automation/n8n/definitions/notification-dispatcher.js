'use strict';
const { Workflow, scheduleTrigger, webhookTrigger, subWorkflowTrigger, api, report, ifTrue, switchOn, sendEmail, sendTelegram, code } = require('../lib/wf');
const { codeNode, markStart, runFinishedBody } = require('./common');
const registry = require('./registry');

const KEY = 'notification-dispatcher';

// Sends what the API has queued (email / Telegram) and reports every outcome, so a delivery failure is retried by the
// API's own schedule and is visible - never silently lost. Runs every minute and whenever another workflow asks.
module.exports = function build() {
  const { id, name } = registry[KEY];
  const wf = new Workflow({ id, key: KEY, name, errorWorkflowId: registry['error-handler'].id, description: 'Delivers queued notifications.' });

  const every = wf.add(scheduleTrigger('Every minute', { minutes: 1 }), [0, 0]);
  const hook = wf.add(webhookTrigger('Run now (webhook)', 'commitmentos-dispatch'), [0, 1]);
  const called = wf.add(subWorkflowTrigger('Called by another workflow'), [0, 2]);
  const start = wf.add(markStart(), [1, 1]);
  [every, hook, called].forEach((t) => wf.link(t, start));

  const claim = wf.add(api('Claim notifications', 'POST', '/api/internal/notifications/claim', { body: '{ limit: 25 }' }), [2, 1]);
  const split = wf.add(
    codeNode(
      'Split claimed',
      'dispatch.js',
      "const items = claimedItems($input.first().json);\nreturn items.length ? items.map((json) => ({ json })) : [{ json: { _idle: true } }];",
      { notes: 'One item per claimed notification, or a single idle marker when nothing is queued.' },
    ),
    [3, 1],
  );
  const work = wf.add(ifTrue('Anything to send?', '{{ !$json._idle }}'), [4, 1]);
  const idle = wf.add(code('Nothing to send', 'return [{ json: { idle: true, sent: 0 } }];'), [5, 3]);
  const channel = wf.add(switchOn('Channel', '{{ $json.channel }}', ['EMAIL', 'TELEGRAM']), [5, 1]);
  wf.chain(start, claim, split, work);
  wf.link(work, channel, 0);
  wf.link(work, idle, 1);

  const email = wf.add(
    sendEmail('Send email', { from: '{{ $json.from_email }}', to: '{{ $json.to_email }}', subject: '{{ $json.subject }}', text: '{{ $json.text }}', html: '{{ $json.html }}' }),
    [6, 0],
  );
  const telegram = wf.add(sendTelegram('Send Telegram', { chatId: '{{ $json.telegram_chat_id }}', text: '{{ $json.subject }}\n\n{{ $json.text }}' }), [6, 2]);
  wf.link(channel, email, 0);
  wf.link(channel, telegram, 1);

  // A Telegram send that "succeeds" without a message id (node disabled / not connected) must not be reported as delivered.
  const telegramCheck = wf.add(
    code('Telegram really sent?', "return $input.all().map((i) => { if (!i.json.message_id) throw new Error('Telegram did not confirm the message (is the bot connected?)'); return i; });", { errorOutput: true }),
    [7, 2],
  );
  wf.link(telegram, telegramCheck, 0);

  // itemMatching() walks the item lineage back to the claimed notification, whatever the send node emitted
  const sent = wf.add(
    codeNode('Sent', 'dispatch.js', "return $input.all().map((_, n) => ({ json: deliveryReport($('Split claimed').itemMatching(n).json, 'sent', $execution.id) }));"),
    [7, 0],
  );
  const failed = wf.add(
    codeNode('Failed', 'dispatch.js', "return $input.all().map((i, n) => ({ json: deliveryReport({ ...$('Split claimed').itemMatching(n).json, error: i.json.error }, 'failed', $execution.id) }));"),
    [7, 2],
  );
  wf.link(email, sent, 0);
  wf.link(email, failed, 1);
  wf.link(telegramCheck, sent, 0);
  wf.link(telegramCheck, failed, 1);
  wf.link(telegram, failed, 1);

  const deliver = wf.add(report('Report delivery', '$json', { retries: 3 }), [8, 1]);
  wf.link(sent, deliver);
  wf.link(failed, deliver);

  const summary = wf.add(
    codeNode('Summarise', 'dispatch.js', "return [{ json: summariseDispatch($input.all().map((i) => ({ event: i.json.event, error: null }))) }];"),
    [9, 1],
  );
  const finish = wf.add(
    report(
      'Report run',
      runFinishedBody(KEY, {
        trigger: "$('Mark start').first().json._trigger",
        status: '$json.status',
        result: '{ sent: $json.sent, failed: $json.failed, errors: $json.errors }',
        error: "$json.failed ? $json.errors.join('; ') : null",
      }),
      { retries: 3, bestEffort: true },
    ),
    [10, 1],
  );
  wf.chain(deliver, summary, finish);
  return wf;
};
