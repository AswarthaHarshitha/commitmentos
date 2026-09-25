'use strict';
const { Workflow, scheduleTrigger, webhookTrigger, api, report, ifTrue, switchOn, sendEmail, code, node } = require('../lib/wf');
const { codeNode, markStart, runFinishedBody } = require('./common');
const registry = require('./registry');

const KEY = 'approved-actions';
const SRC = "$('Split approvals')"; // the claimed approval each downstream item came from (via item lineage)

// Human approval, then execution. n8n only ever runs actions a PERSON approved: the API hands them over by claiming
// them atomically (APPROVED -> EXECUTING), so a duplicate webhook or a second poll can never run one twice. It works
// two ways: the API pushes a webhook the moment the user approves, and a schedule polls in case n8n was down.
module.exports = function build() {
  const { id, name } = registry[KEY];
  const wf = new Workflow({ id, key: KEY, name, errorWorkflowId: registry['error-handler'].id, description: 'Executes actions a person has approved.' });

  const pushed = wf.add(webhookTrigger('Approved by the user (webhook)', 'commitmentos-action'), [0, 0]);
  const poll = wf.add(scheduleTrigger('Every 2 minutes (catch-up)', { minutes: 2 }), [0, 2]);
  const start = wf.add(markStart(), [1, 1]);
  wf.link(pushed, start);
  wf.link(poll, start);

  const single = wf.add(ifTrue('Named in the webhook?', '{{ !!($json.body && $json.body.approval_id) }}'), [2, 1]);
  const claimOne = wf.add(api('Claim this approval', 'GET', '/api/internal/approvals/{{ $json.body.approval_id }}', { retries: 3 }), [3, 0]);
  const claimMany = wf.add(api('Claim approved actions', 'POST', '/api/internal/approvals/claim', { body: '{ limit: 10 }', retries: 3 }), [3, 2]);
  wf.chain(start, single);
  wf.link(single, claimOne, 0);
  wf.link(single, claimMany, 1);

  const split = wf.add(
    codeNode('Split approvals', 'actions.js', 'const items = claimedApprovals($input.first().json);\nreturn items.length ? items.map((json) => ({ json, pairedItem: { item: 0 } })) : [{ json: { _idle: true } }];', {
      notes: 'One item per claimed approval, or an idle marker when there is nothing to run.',
    }),
    [4, 1],
  );
  const work = wf.add(ifTrue('Anything to run?', '{{ !$json._idle }}'), [5, 1]);
  const idle = wf.add(code('Nothing to run', 'return [{ json: { idle: true, executed: 0, n8n_execution_id: String($execution.id) } }];'), [6, 3]);
  wf.link(claimOne, split);
  wf.link(claimMany, split);
  wf.chain(split, work);
  wf.link(work, idle, 1);

  const action = wf.add(switchOn('Which action?', '{{ $json.action_type }}', ['SEND_FOLLOW_UP', 'CREATE_CALENDAR_EVENT']), [6, 1]);
  wf.link(work, action, 0);

  // ---- follow-up email, sent from the system address with the user as Reply-To ------------------------------------------
  const followUp = wf.add(
    sendEmail('Send follow-up', { from: '{{ $json.from_email }}', to: '{{ $json.payload.to }}', replyTo: '{{ $json.user_email }}', subject: '{{ $json.payload.subject }}', text: '{{ $json.payload.body }}' }),
    [7, 0],
  );
  wf.link(action, followUp, 0);

  // ---- calendar event: a real .ics invite by email (local), or Google Calendar when connected -------------------------------
  const provider = wf.add(ifTrue('Google Calendar?', '{{ $json.calendar_provider === "google" }}'), [7, 2]);
  wf.link(action, provider, 1);
  const invite = wf.add(
    codeNode(
      'Build invite',
      'actions.js',
      "return $input.all().map((i) => {\n  const mail = inviteMail(i.json, new Date().toISOString());\n  return { json: { from: mail.from, to: mail.to, subject: mail.subject, text: mail.text }, binary: { invite: { data: Buffer.from(mail.ics, 'utf8').toString('base64'), mimeType: 'text/calendar', fileName: 'commitment.ics' } }, pairedItem: { item: 0 } };\n});",
    ),
    [8, 3],
  );
  const emailInvite = wf.add(sendEmail('Email the invite', { from: '{{ $json.from }}', to: '{{ $json.to }}', subject: '{{ $json.subject }}', text: '{{ $json.text }}', attachments: 'invite' }), [9, 3]);
  wf.link(provider, invite, 1);
  wf.link(invite, emailInvite);

  const google = wf.add(
    node(
      'Google Calendar: create event',
      'n8n-nodes-base.googleCalendar',
      1.3,
      {
        resource: 'event',
        operation: 'create',
        calendar: { __rl: true, mode: 'id', value: 'primary' },
        start: '={{ $json.payload.start_at }}',
        end: '={{ $json.payload.end_at }}',
        additionalFields: { summary: '={{ $json.payload.title }}', description: '={{ $json.payload.description }}' },
      },
      { disabled: true, onError: 'continueErrorOutput', notes: 'NOT VERIFIED - needs a Google Calendar OAuth credential (connect it in n8n, then enable this node).', notesInFlow: true },
    ),
    [8, 1],
  );
  // a disabled or unconnected Google node passes its input straight through; that must never count as "created"
  const googleCheck = wf.add(
    code('Google really created it?', "return $input.all().map((i) => { if (i.json.kind !== 'calendar#event') throw new Error('Google Calendar did not return an event (is the node enabled and connected?)'); return i; });", { errorOutput: true }),
    [9, 1],
  );
  wf.link(provider, google, 0);
  wf.link(google, googleCheck, 0);

  // ---- outcome -> report -> summary ---------------------------------------------------------------------------------------
  const executed = wf.add(
    codeNode(
      'Executed',
      'actions.js',
      `return $input.all().map((i, n) => {\n  const src = ${SRC}.itemMatching(n).json;\n  const result = src.action_type === 'SEND_FOLLOW_UP' ? { message_id: i.json.messageId || null, accepted: i.json.accepted || [] }\n    : src.calendar_provider === 'google' ? googleCalendarResult(i.json) : localCalendarResult(src);\n  return { json: executionReport(src, 'executed', result, $execution.id) };\n});`,
    ),
    [10, 1],
  );
  const failed = wf.add(
    codeNode('Failed', 'actions.js', `return $input.all().map((i, n) => ({ json: executionReport(${SRC}.itemMatching(n).json, 'failed', i.json.error || i.json, $execution.id) }));`),
    [10, 2],
  );
  wf.link(followUp, executed, 0);
  wf.link(followUp, failed, 1);
  wf.link(emailInvite, executed, 0);
  wf.link(emailInvite, failed, 1);
  wf.link(googleCheck, executed, 0);
  wf.link(googleCheck, failed, 1);
  wf.link(google, failed, 1);

  const outcome = wf.add(report('Report outcome', '$json', { retries: 3 }), [11, 1]);
  wf.link(executed, outcome);
  wf.link(failed, outcome);
  const summarise = wf.add(
    codeNode('Summarise', 'actions.js', "return [{ json: summariseActions($input.all().map((i) => ({ event: i.json.event, error: i.json.error || null }))) }];"),
    [12, 1],
  );
  const finish = wf.add(
    report(
      'Report run',
      runFinishedBody(KEY, {
        trigger: "$('Mark start').first().json._trigger",
        status: '$json.status',
        result: '{ executed: $json.executed, failed: $json.failed }',
        error: "$json.failed ? $json.errors.join('; ') : null",
      }),
      { retries: 3, bestEffort: true },
    ),
    [13, 1],
  );
  const result = wf.add(code('Result', "return [{ json: { ...$('Summarise').first().json, n8n_execution_id: String($execution.id) } }];"), [14, 1]);
  wf.chain(outcome, summarise, finish, result);
  return wf;
};
