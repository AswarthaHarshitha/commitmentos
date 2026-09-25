'use strict';
// Unit tests for the Code-node logic. They evaluate the exact source that build.js deploys (see lib/load-code.js).
// Run: node --test automation/n8n/tests
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadCode } = require('../lib/load-code');

const N = loadCode('normalize-message.js');
const I = loadCode('ingestion.js');
const D = loadCode('dispatch.js');
const A = loadCode('actions.js');
const S = loadCode('scans.js');
const M = loadCode('monitor.js');
const E = loadCode('errors.js');

const NOW = '2026-09-24T12:00:00.000Z';
const webhook = (message, extra = {}) => ({ body: { user_email: 'Alice@Example.com', message, ...extra }, headers: {} });
const msg = (over = {}) => ({ source_type: 'demo', external_id: 'm-1', sender_email: 'HR@Example.org', subject: 'Paperwork', body: 'Please sign by Friday.', received_at: '2026-09-23T15:00:00Z', ...over });

// ================================================================================== normalize: the ingest webhook
test('a webhook message is normalised into the API envelope', () => {
  const out = N.normalizeIncoming(webhook(msg()), { nowIso: NOW });
  assert.equal(out.user_email, 'alice@example.com');
  assert.equal(out.trigger, 'WEBHOOK');
  assert.equal(out.correlation_id, 'm-1');
  assert.deepEqual(out.message, {
    source_type: 'DEMO', external_id: 'm-1', thread_id: null, rfc_message_id: null, sender_email: 'hr@example.org', sender_name: null,
    subject: 'Paperwork', body: 'Please sign by Friday.', received_at: '2026-09-23T15:00:00.000Z', direction: 'INBOUND', recipients: [], is_synthetic: true,
  });
});

test('a missing received_at falls back to now, and a garbage one does too', () => {
  assert.equal(N.normalizeIncoming(webhook(msg({ received_at: undefined })), { nowIso: NOW }).message.received_at, NOW);
  assert.equal(N.normalizeIncoming(webhook(msg({ received_at: 'not a date' })), { nowIso: NOW }).message.received_at, NOW);
});

test('direction: explicit OUTBOUND, or sent by the mailbox owner', () => {
  assert.equal(N.normalizeIncoming(webhook(msg({ direction: 'OUTBOUND' })), { nowIso: NOW }).message.direction, 'OUTBOUND');
  assert.equal(N.normalizeIncoming(webhook(msg({ sender_email: 'ALICE@example.com' })), { nowIso: NOW }).message.direction, 'OUTBOUND');
  assert.equal(N.normalizeIncoming(webhook(msg({ direction: 'sideways' })), { nowIso: NOW }).message.direction, 'INBOUND');
});

test('recipients are validated, lower-cased and capped', () => {
  const many = Array.from({ length: 30 }, (_, i) => `p${i}@Example.com`);
  const out = N.normalizeIncoming(webhook(msg({ recipients: ['Bob@Example.com', 'not-an-email', 5, ...many] })), { nowIso: NOW }).message.recipients;
  assert.equal(out[0], 'bob@example.com');
  assert.equal(out.length, 20);
  assert.ok(out.every((r) => /^[^@]+@example\.com$/.test(r)));
});

for (const [why, payload, fragment] of [
  ['no message', { body: { user_email: 'a@example.com' } }, /Unrecognised|"message" is missing/],
  ['message is not an object', { body: { user_email: 'a@example.com', message: 'hi' } }, /Unrecognised|"message" is missing/],
  ['no user email', webhook(msg(), { user_email: undefined }), /user_email/],
  ['bad user email', webhook(msg(), { user_email: 'nope' }), /user_email/],
  ['no external id', webhook(msg({ external_id: undefined })), /external_id/],
  ['blank external id', webhook(msg({ external_id: '   ' })), /external_id/],
  ['external id too long', webhook(msg({ external_id: 'x'.repeat(513) })), /external_id/],
  ['no body and no subject', webhook(msg({ body: '  ', subject: null })), /neither a body nor a subject/],
]) {
  test(`an invalid ingest payload is rejected clearly: ${why}`, () => {
    assert.throws(() => N.normalizeIncoming(payload, { nowIso: NOW }), fragment);
  });
}

test('an item that is neither an ingest payload nor a Gmail message is refused', () => {
  assert.throws(() => N.normalizeIncoming({ hello: 'world' }), /Unrecognised incoming item/);
  assert.throws(() => N.normalizeIncoming(null), /Unrecognised incoming item/);
});

test('a subject-only message is allowed (the subject can carry the request)', () => {
  const out = N.normalizeIncoming(webhook(msg({ body: '', subject: 'Reminder: pay rent by the 1st' })), { nowIso: NOW });
  assert.equal(out.message.body, '');
  assert.equal(out.message.subject, 'Reminder: pay rent by the 1st');
});

// ================================================================================== normalize: cleaning
test('html is converted to readable text, entities decoded once', () => {
  const html = '<html><style>p{color:red}</style><body><p>Pay &lt;$40&gt; by <b>Friday</b>&nbsp;5pm.</p><script>alert(1)</script><p>R&amp;D &amp;lt; team</p></body></html>';
  const text = N.htmlToText(html);
  assert.match(text, /Pay <\$40> by Friday 5pm\./);
  assert.match(text, /R&D &lt; team/); // "&amp;lt;" is the literal text "&lt;", not "<"
  assert.doesNotMatch(text, /alert|color:red/);
});

test('numeric character references are decoded and out-of-range ones are ignored', () => {
  assert.equal(N.htmlToText('caf&#233; &#99999999;').trim(), 'café');
});

test('quoted reply history is removed, so an old request is not detected again', () => {
  const text = 'Thanks, will do.\n\nOn Tue, Sep 22, 2026 at 9:14 AM Dana <dana@example.org> wrote:\n> Please send the signed form by Friday.\n> Thanks';
  assert.equal(N.cleanText(text, null), 'Thanks, will do.');
});

test('lines starting with > are dropped even without a marker', () => {
  assert.equal(N.cleanText('Sounds good, thanks a lot for the update.\n> quoted line\nSee you then.', null), 'Sounds good, thanks a lot for the update.\nSee you then.');
});

test('a forward keeps its content: recognised by its subject, or by a marker with nothing above it', () => {
  const quoted = 'FYI\n\nOn Tue, Sep 22, 2026 at 9:14 AM Dana <dana@example.org> wrote:\n> Please send the signed form by Friday.';
  assert.match(N.cleanText(quoted, null, 'Fwd: Signed form'), /Please send the signed form by Friday/);
  assert.match(N.cleanText(quoted, null, 'FW: Signed form'), /Please send the signed form by Friday/);
  const topMarker = '-----Original Message-----\nFrom: Dana\nPlease send the signed form by Friday.';
  assert.match(N.cleanText(topMarker, null, 'Signed form'), /Please send the signed form by Friday/);
  assert.doesNotMatch(N.cleanText(quoted, null, 'Re: Signed form'), /signed form by Friday/); // the same text as a reply IS stripped
});

test('a short reply above quoted history is still a reply', () => {
  assert.equal(N.cleanText('Yes!\n\nOn Tue, Sep 22, 2026 at 9:14 AM Dana <dana@example.org> wrote:\n> Can you make Friday?', null, 'Re: Friday'), 'Yes!');
});

test('signatures and mobile footers are trimmed', () => {
  assert.equal(N.cleanText('Please review the contract before Monday morning, thank you.\n-- \nDana Whitfield\nUniversity HR\nSent from my iPhone', null), 'Please review the contract before Monday morning, thank you.');
});

test('invisible characters are removed and blank lines collapsed', () => {
  assert.equal(N.cleanText('Pay\u200b now\r\n\r\n\r\n\r\nplease\ufeff', null), 'Pay now\n\nplease');
});

test('text wins over html when both exist; html is used when text is empty', () => {
  assert.equal(N.cleanText('plain', '<p>rich</p>'), 'plain');
  assert.equal(N.cleanText('   ', '<p>rich</p>').trim(), 'rich');
});

test('an oversized body is capped so a huge newsletter cannot flood the workflow', () => {
  assert.equal(N.cleanText('x'.repeat(N.MAX_BODY + 5000), null).length, N.MAX_BODY);
});

// ================================================================================== normalize: the Gmail path
const gmail = (over = {}) => ({
  id: '18c7', threadId: 't-9', labelIds: ['INBOX'], subject: 'Signed offer letter', internalDate: String(Date.UTC(2026, 8, 23, 15, 0)),
  from: { value: [{ address: 'Dana@Example.org', name: 'Dana Whitfield' }], text: 'Dana Whitfield <dana@example.org>' },
  to: { value: [{ address: 'alice@example.com' }] }, cc: { value: [{ address: 'lead@example.com' }] },
  text: 'Please send it back by Friday 5pm.', messageId: '<abc@mail.example.org>', ...over,
});

test('a Gmail message is normalised, with the mailbox owner supplied by the workflow', () => {
  const out = N.normalizeIncoming(gmail(), { ownerEmail: 'Alice@example.com', nowIso: NOW });
  assert.equal(out.trigger, 'GMAIL');
  assert.equal(out.user_email, 'alice@example.com');
  assert.deepEqual(out.message, {
    source_type: 'GMAIL', external_id: '18c7', thread_id: 't-9', rfc_message_id: '<abc@mail.example.org>', sender_email: 'dana@example.org', sender_name: 'Dana Whitfield',
    subject: 'Signed offer letter', body: 'Please send it back by Friday 5pm.', received_at: '2026-09-23T15:00:00.000Z', direction: 'INBOUND',
    recipients: ['alice@example.com', 'lead@example.com'], is_synthetic: false,
  });
});

test('a Gmail message the owner sent is OUTBOUND', () => {
  const out = N.normalizeIncoming(gmail({ from: { value: [{ address: 'alice@example.com' }] } }), { ownerEmail: 'alice@example.com', nowIso: NOW });
  assert.equal(out.message.direction, 'OUTBOUND');
});

test('the Gmail path refuses to guess whose mailbox this is', () => {
  assert.throws(() => N.normalizeIncoming(gmail(), { ownerEmail: null }), /mailbox owner/);
  assert.throws(() => N.normalizeIncoming(gmail(), { ownerEmail: 'not-an-email' }), /mailbox owner/);
});

test('sender parsing tolerates the text form and missing pieces', () => {
  assert.deepEqual(N.parseAddress({ text: 'Dana W <Dana@Example.org>' }), { email: 'dana@example.org', name: 'Dana W' });
  assert.deepEqual(N.parseAddress('dana@example.org'), { email: 'dana@example.org', name: null });
  assert.deepEqual(N.parseAddress(null), { email: null, name: null });
  assert.deepEqual(N.parseAddress({ text: 'garbage' }), { email: null, name: null });
});

test('dates: epoch milliseconds, then a date header, else nothing', () => {
  assert.equal(N.isoDate('1758639600000', null), '2025-09-23T15:00:00.000Z');
  assert.equal(N.isoDate(null, 'Wed, 23 Sep 2026 15:00:00 +0000'), '2026-09-23T15:00:00.000Z');
  assert.equal(N.isoDate(null, 'yesterday-ish'), null);
});

// ================================================================================== ingestion reports
const prepared = { user_email: 'alice@example.com', trigger: 'WEBHOOK', correlation_id: 'm-1', message: { external_id: 'm-1', body: 'b' } };
const okExtract = { status: 'OK', extraction: { title: 'Sign' }, analysis: { resolution: {} }, provider: 'gemini', model: 'g3', attempts: 1, latency_ms: 900, decision: { action: 'CREATE' } };

test('a successful extraction becomes a message.extracted event carrying the proposal', () => {
  const ev = I.buildExtractionReport(okExtract, prepared, 4711);
  assert.equal(ev.event, 'message.extracted');
  assert.equal(ev.n8n_execution_id, '4711');
  assert.equal(ev.extraction_status, 'OK');
  assert.equal(ev.extraction_error, null);
  assert.deepEqual(ev.extraction, { title: 'Sign' });
  assert.deepEqual(ev.message, prepared.message);
});

test('a non-OK extraction is reported without a proposal and with a redacted, bounded error', () => {
  const ev = I.buildExtractionReport({ status: 'LLM_QUOTA_EXHAUSTED', error_message: `quota spent; token=AIzaSyA-1234567890abcdefghij ${'x'.repeat(2000)}`, extraction: { title: 'x' } }, prepared, 1);
  assert.equal(ev.extraction_status, 'LLM_QUOTA_EXHAUSTED');
  assert.equal(ev.extraction, null);
  assert.equal(ev.analysis, null);
  assert.ok(ev.extraction_error.length <= 900);
  assert.doesNotMatch(ev.extraction_error, /AIza/);
});

test('an LLM that never answered is reported as LLM_UNAVAILABLE with the reason', () => {
  const ev = I.buildFailureReport({ message: 'The service is receiving too many requests', httpCode: 503 }, prepared, 2);
  assert.equal(ev.extraction_status, 'LLM_UNAVAILABLE');
  assert.match(ev.extraction_error, /503/);
  assert.equal(ev.extraction, null);
});

test('the summary distinguishes processed, duplicate and failed', () => {
  const created = I.summariseIngestion({ disposition: 'OBLIGATION_CREATED', obligation_id: 'ob-1', status: 'OPEN', notifications: 2, due_at: '2026-09-25T21:00:00Z', confidence: 0.95, decision: { action: 'CREATE' } }, okExtract, prepared);
  assert.equal(created.outcome, 'processed');
  assert.equal(created.decision, 'CREATE');
  assert.equal(created.notifications, 2);
  assert.equal(created.check_completion, true);
  assert.equal(created.failed, false);

  const dup = I.summariseIngestion({ disposition: 'OBLIGATION_CREATED', duplicate_event: true, obligation_id: 'ob-1' }, okExtract, prepared);
  assert.equal(dup.outcome, 'duplicate');
  assert.equal(dup.check_completion, false);

  const failed = I.summariseIngestion({ disposition: 'EXTRACTION_FAILED', status: 'LLM_UNAVAILABLE', error: 'down' }, null, prepared);
  assert.equal(failed.outcome, 'failed');
  assert.equal(failed.failed, true);
  assert.equal(failed.error, 'down');
  assert.equal(failed.check_completion, false);
  assert.equal(failed.llm, null);
});

test('a message already seen is a skipped duplicate that never reaches the LLM', () => {
  const s = I.skippedDuplicate({ seen: true, disposition: 'OBLIGATION_CREATED', obligation_id: 'ob-1' }, prepared);
  assert.equal(s.outcome, 'duplicate');
  assert.equal(s.obligation_id, 'ob-1');
  assert.equal(s.llm, null);
});

test('run events: SUCCESS, SKIPPED for a duplicate, and run.failed for a failed extraction', () => {
  const started = Date.now() - 1500;
  const ok = I.runFinishedEvent(I.summariseIngestion({ disposition: 'OBLIGATION_CREATED', obligation_id: 'ob-1' }, okExtract, prepared), prepared, 9, 'wf1', 'Commitment Detection', started);
  assert.deepEqual([ok.event, ok.status, ok.obligation_id, ok.workflow_key], ['run.finished', 'SUCCESS', 'ob-1', 'incoming-detection']);
  assert.ok(ok.duration_ms >= 1500);

  const skip = I.runFinishedEvent(I.skippedDuplicate({ seen: true }, prepared), prepared, 9, 'wf1', 'x', started);
  assert.equal(skip.status, 'SKIPPED');
  assert.equal('obligation_id' in skip, false);

  const bad = I.runFinishedEvent(I.summariseIngestion({ disposition: 'EXTRACTION_FAILED', error: 'LLM down' }, null, prepared), prepared, 9, 'wf1', 'x', started);
  assert.deepEqual([bad.event, bad.status, bad.error, bad.error_node], ['run.failed', 'FAILED', 'LLM down', 'Extract (LLM)']);
});

// ================================================================================== dispatch
test('delivery reports: sent, and failed with a redacted reason', () => {
  assert.deepEqual(D.deliveryReport({ id: 'n1' }, 'sent', 5), { event: 'notification.sent', notification_id: 'n1', n8n_execution_id: '5' });
  const failed = D.deliveryReport({ id: 'n2', error: { message: 'Invalid login: 535 password=hunter2hunter2' } }, 'failed', 5);
  assert.equal(failed.event, 'notification.failed');
  assert.match(failed.error, /Invalid login/);
  assert.doesNotMatch(failed.error, /hunter2/);
});

test('a claim reply is unwrapped safely', () => {
  assert.deepEqual(D.claimedItems({ items: [{ id: 1 }], count: 1 }), [{ id: 1 }]);
  assert.deepEqual(D.claimedItems({}), []);
  assert.deepEqual(D.claimedItems(null), []);
});

test('the dispatch summary grades the run', () => {
  const ok = { event: 'notification.sent' };
  const bad = { event: 'notification.failed', error: 'x' };
  assert.equal(D.summariseDispatch([ok, ok]).status, 'SUCCESS');
  assert.equal(D.summariseDispatch([ok, bad]).status, 'PARTIAL');
  assert.equal(D.summariseDispatch([bad]).status, 'FAILED');
  assert.deepEqual(D.summariseDispatch([ok, bad]), { total: 2, sent: 1, failed: 1, errors: ['x'], status: 'PARTIAL' });
});

// ================================================================================== approved actions
const cal = { id: 'ap-1', user_email: 'alice@example.com', from_email: 'commitmentos@localhost', payload: { title: 'Due: Sign, review; approve', start_at: '2026-09-25T20:30:00-04:00', end_at: '2026-09-25T21:00:00-04:00', timezone: 'America/New_York', description: 'Line one\nLine two, with; punctuation \\ done' } };

test('claims are unwrapped for both the push and the pull path', () => {
  assert.deepEqual(A.claimedApprovals({ claimed: true, item: { id: 'a' } }), [{ id: 'a' }]);
  assert.deepEqual(A.claimedApprovals({ items: [{ id: 'a' }, { id: 'b' }], count: 2 }), [{ id: 'a' }, { id: 'b' }]);
  assert.deepEqual(A.claimedApprovals({ claimed: false, reason: 'status is EXECUTED' }), []);
  assert.deepEqual(A.claimedApprovals(undefined), []);
});

test('a follow-up mail is sent from the system address on behalf of the user, who receives the replies', () => {
  const mail = A.followUpMail({ from_email: 'commitmentos@localhost', user_email: 'alice@example.com', payload: { to: 'dana@example.org', subject: 'Following up', body: 'Hi Dana' } });
  assert.deepEqual(mail, { from: 'commitmentos@localhost', to: 'dana@example.org', replyTo: 'alice@example.com', subject: 'Following up', text: 'Hi Dana' });
});

test('the calendar invite is valid iCalendar: CRLF lines, UTC times, escaping, an alarm', () => {
  const ics = A.buildIcs(cal, '2026-09-24T12:00:00Z');
  assert.ok(ics.endsWith('\r\n'));
  assert.ok(ics.split('\r\n').slice(0, -1).every((l) => !l.includes('\n')));
  const unfolded = ics.replace(/\r\n /g, '');
  assert.match(unfolded, /^BEGIN:VCALENDAR\r\nVERSION:2\.0\r\n/);
  assert.match(unfolded, /UID:approval-ap-1@commitmentos\r\n/);
  assert.match(unfolded, /DTSTAMP:20260924T120000Z\r\n/);
  assert.match(unfolded, /DTSTART:20260926T003000Z\r\n/); // 20:30 EDT is 00:30Z the next day
  assert.match(unfolded, /DTEND:20260926T010000Z\r\n/);
  assert.match(unfolded, /SUMMARY:Due: Sign\\, review\\; approve\r\n/);
  assert.match(unfolded, /DESCRIPTION:Line one\\nLine two\\, with\\; punctuation \\\\ done\r\n/);
  assert.match(unfolded, /BEGIN:VALARM\r\nACTION:DISPLAY[\s\S]*TRIGGER:-PT15M\r\nEND:VALARM/);
  assert.match(unfolded, /END:VEVENT\r\nEND:VCALENDAR\r\n$/);
});

test('long lines are folded at 75 octets without splitting a multi-byte character', () => {
  const folded = A.icsFold(`SUMMARY:${'é'.repeat(80)}`);
  const lines = folded.split('\r\n');
  assert.ok(lines.length > 1);
  assert.ok(lines.every((l) => Buffer.byteLength(l, 'utf8') <= 75), 'every physical line, including its leading space, is at most 75 octets');
  assert.equal(lines.map((l, i) => (i ? l.slice(1) : l)).join(''), `SUMMARY:${'é'.repeat(80)}`); // nothing lost, nothing corrupted
  assert.equal(A.icsFold('SUMMARY:short'), 'SUMMARY:short');
});

test('an invalid time in the payload fails loudly instead of producing a bad invite', () => {
  assert.throws(() => A.buildIcs({ ...cal, payload: { ...cal.payload, start_at: 'soon' } }), /invalid time/);
  assert.throws(() => A.buildIcs({ ...cal, payload: { title: 't' } }), /invalid time/);
});

test('the invite email goes to the user, with the ICS attached as text', () => {
  const mail = A.inviteMail(cal, '2026-09-24T12:00:00Z');
  assert.equal(mail.to, 'alice@example.com');
  assert.equal(mail.from, 'commitmentos@localhost');
  assert.match(mail.subject, /Calendar invite: Due: Sign/);
  assert.match(mail.ics, /^BEGIN:VCALENDAR/);
});

test('execution reports and calendar results', () => {
  assert.deepEqual(A.executionReport({ id: 'ap-1' }, 'executed', A.localCalendarResult(cal), 8), {
    event: 'approval.executed', approval_id: 'ap-1', n8n_execution_id: '8', result: { provider: 'LOCAL', external_id: 'local-ap-1', delivered_to: 'alice@example.com' },
  });
  const bad = A.executionReport({ id: 'ap-1' }, 'failed', { message: 'Connection refused' }, 8);
  assert.deepEqual([bad.event, bad.error], ['approval.failed', 'Connection refused']);
  assert.deepEqual(A.googleCalendarResult({ id: 'evt1', htmlLink: 'https://calendar.example/e/1' }), { provider: 'GOOGLE', external_id: 'evt1', url: 'https://calendar.example/e/1' });
});

test('the actions summary grades the run', () => {
  const ok = { event: 'approval.executed' };
  const bad = { event: 'approval.failed', error: 'x' };
  assert.equal(A.summariseActions([ok]).status, 'SUCCESS');
  assert.equal(A.summariseActions([ok, bad]).status, 'PARTIAL');
  assert.equal(A.summariseActions([bad]).status, 'FAILED');
});

// ================================================================================== scans / proposals
test('a scan item becomes a proposal.created event for a person to decide on', () => {
  const ev = S.proposalEvent({ obligation_id: 'ob-1', action_type: 'SEND_FOLLOW_UP', title: 'Send a follow-up to Dana', rationale: 'r', payload: { to: 'd@example.org' } }, 77);
  assert.deepEqual(ev, { event: 'proposal.created', obligation_id: 'ob-1', action_type: 'SEND_FOLLOW_UP', title: 'Send a follow-up to Dana', rationale: 'r', payload: { to: 'd@example.org' }, proposed_by: 'SYSTEM', n8n_execution_id: '77' });
});

test('proposal fields are bounded to what the API accepts', () => {
  const ev = S.proposalEvent({ obligation_id: 'ob-1', action_type: 'CREATE_CALENDAR_EVENT', title: 't'.repeat(500), rationale: 'r'.repeat(2000) }, 1);
  assert.equal(ev.title.length, 300);
  assert.equal(ev.rationale.length, 1000);
  assert.deepEqual(ev.payload, {});
  assert.equal(S.proposalEvent({ obligation_id: 'o', action_type: 'X', title: 't' }, 1).rationale, null);
});

test('a completion match becomes a proposal to mark the commitment done, carrying the evidence', () => {
  const ev = S.completionProposal({ obligation_id: 'ob-1', obligation_title: 'Submit documents', rationale: 'HR confirms receipt. Evidence: "we received your documents"' }, 5);
  assert.deepEqual([ev.action_type, ev.proposed_by, ev.title, ev.obligation_id], ['COMPLETE_OBLIGATION', 'AI', 'Mark as done: Submit documents', 'ob-1']);
  assert.match(ev.rationale, /we received your documents/);
});

test('proposal outcomes are counted, including ones that already existed', () => {
  assert.deepEqual(S.summariseProposals([{ created: true }, { created: false }, { created: true }, null]), { proposals: 4, created: 2, already_open: 2 });
  assert.deepEqual(S.summariseProposals([]), { proposals: 0, created: 0, already_open: 0 });
  assert.deepEqual(S.scanItems({ items: [1, 2] }), [1, 2]);
  assert.deepEqual(S.scanItems({}), []);
});

test('completion assessment', () => {
  assert.deepEqual(S.assessCompletion({ status: 'OK', candidates: 2, llm_called: true, matches: [{}] }), { status: 'OK', candidates: 2, llm_called: true, matches: 1 });
  assert.deepEqual(S.assessCompletion({ status: 'OK' }), { status: 'OK', candidates: 0, llm_called: false, matches: 0 });
});

// ================================================================================== monitor
test('an idle tick is not worth recording, and does not ask for delivery', () => {
  const a = M.assessTick({ evaluated: 3, reminders_queued: 0, marked_overdue: 0, escalated: 0, review_nudges: 0, approvals_expired: 0, stale_detected_swept: 0, notifications_suppressed: 0, errors: [] });
  assert.deepEqual([a.did_work, a.deliver, a.status], [false, false, 'SUCCESS']);
  assert.equal(M.assessTick({}).did_work, false);
  assert.equal(M.assessTick(null).did_work, false);
});

test('reminders, overdue, escalation and nudges all trigger delivery', () => {
  for (const k of ['reminders_queued', 'marked_overdue', 'escalated', 'review_nudges']) {
    const a = M.assessTick({ [k]: 1 });
    assert.equal(a.did_work, true, k);
    assert.equal(a.deliver, true, k);
  }
});

test('housekeeping is recorded but has nothing to deliver', () => {
  for (const k of ['approvals_expired', 'stale_detected_swept', 'notifications_suppressed']) {
    const a = M.assessTick({ [k]: 2 });
    assert.deepEqual([a.did_work, a.deliver], [true, false], k);
  }
});

test('errors inside the tick make it PARTIAL, with a redacted message', () => {
  const a = M.assessTick({ reminders_queued: 1, errors: ['obligation 7: boom token=abcdefghijk123'] });
  assert.equal(a.status, 'PARTIAL');
  assert.match(a.error, /boom/);
  assert.doesNotMatch(a.error, /abcdefghijk123/);
  assert.equal(a.result.errors, 1);
});

// ================================================================================== error handler
const keys = { wf1: 'incoming-detection', wf2: 'deadline-monitor' };

test('n8n error payloads are shaped into run.failed events for the automation history', () => {
  const ev = E.shapeError({ execution: { id: '812', error: { message: 'The service refused the connection - perhaps it is offline', httpCode: 'ECONNREFUSED' }, lastNodeExecuted: 'Monitor tick' }, workflow: { id: 'wf2', name: 'Deadline Monitor' } }, keys);
  assert.deepEqual(ev, { event: 'run.failed', n8n_execution_id: '812', workflow_key: 'deadline-monitor', n8n_workflow_id: 'wf2', workflow_name: 'Deadline Monitor', status: 'FAILED', error: 'The service refused the connection - perhaps it is offline', error_node: 'Monitor tick' });
});

test('an unknown workflow and a missing execution id still produce a reportable event', () => {
  const ev = E.shapeError({ execution: { error: { message: 'x' } }, workflow: { id: 'zzz' } }, keys, 12345);
  assert.deepEqual([ev.workflow_key, ev.n8n_execution_id, ev.error_node], ['unknown', 'err-12345', null]);
  assert.equal(E.shapeError({}, keys, 1).status, 'FAILED');
});

test('secrets never travel inside error text', () => {
  const ev = E.shapeError({ execution: { id: 1, error: { message: 'POST failed: Authorization: Bearer abcdef1234567890 x-webhook-secret=s3cr3tvalue key AIzaSyA1234567890abcdefghij' } }, workflow: { id: 'wf1' } }, keys);
  assert.doesNotMatch(ev.error, /abcdef1234567890|s3cr3tvalue|AIzaSy/);
  assert.match(ev.error, /redacted/);
});

test('long error messages are bounded', () => {
  assert.ok(E.shapeError({ execution: { id: 1, error: { message: 'e'.repeat(5000) } } }, keys).error.length <= 1500);
});

// ================================================================================== shared helpers
test('errorMessage understands the shapes n8n produces', () => {
  const H = loadCode('errors.js');
  assert.equal(H.shapeError({ execution: { id: 1, error: 'plain string' } }, {}).error, 'plain string');
  assert.equal(H.shapeError({ execution: { id: 1, error: { error: { message: 'nested', httpCode: 503 } } } }, {}).error, 'nested - HTTP 503');
  assert.equal(H.shapeError({ execution: { id: 1 } }, {}).error.length > 0, true);
});

// ================================================================================== HTTP errors never carry a quoted request
test('an HTTP error keeps its status and the API\'s own code, but never an echoed request body', () => {
  const echoed = '422 - "{\\"detail\\":[{\\"type\\":\\"missing\\",\\"loc\\":[\\"body\\",\\"message\\",\\"body\\"],\\"msg\\":\\"Field required\\",\\"input\\":{\\"body\\":\\"My salary is 90000, call bob@example.com\\"}}]}"';
  const ev = E.shapeError({ execution: { id: 1, error: { message: echoed } }, workflow: { id: 'wf1' } }, keys);
  assert.equal(ev.error, 'HTTP 422: the request was invalid (body.message.body)');
  assert.doesNotMatch(ev.error, /salary|bob@/);
});

test('structured API errors keep their code and detail; the LLM failure text survives', () => {
  const structured = '401 - "{\\"detail\\":\\"Invalid webhook secret\\",\\"code\\":\\"INVALID_WEBHOOK_SECRET\\"}"';
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: structured } } }, keys).error, 'HTTP 401: INVALID_WEBHOOK_SECRET: Invalid webhook secret');
  const llm = '503 - "{\\"status\\":\\"LLM_UNAVAILABLE\\",\\"retryable\\":true,\\"error_message\\":\\"The LLM service returned 503\\"}"';
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: llm } } }, keys).error, 'HTTP 503: LLM_UNAVAILABLE: The LLM service returned 503');
});

test('an HTTP error whose body is not JSON is reduced to its status', () => {
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: '502 - <html>Bad gateway for bob@example.com</html>' } } }, keys).error, 'HTTP 502');
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: '500 - "plain text with alice@example.com"' } } }, keys).error, 'HTTP 500');
});

test('errors that are not HTTP errors are left alone', () => {
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: 'connect ECONNREFUSED 172.21.0.3:8000' } } }, keys).error, 'connect ECONNREFUSED 172.21.0.3:8000');
  assert.equal(E.shapeError({ execution: { id: 1, error: { message: 'The service refused the connection - perhaps it is offline' } } }, keys).error, 'The service refused the connection - perhaps it is offline');
});

test('the failure report for an unreachable LLM never quotes the response body either', () => {
  const ev = I.buildFailureReport({ error: { message: '503 - "{\\"status\\":\\"LLM_UNAVAILABLE\\",\\"error_message\\":\\"overloaded\\",\\"input\\":\\"secret email text\\"}"' } }, prepared, 3);
  assert.match(ev.extraction_error, /LLM_UNAVAILABLE/);
  assert.doesNotMatch(ev.extraction_error, /secret email text/);
});

test('redaction targets secrets, not words that merely contain "secret" or "token"', () => {
  const E2 = loadCode('errors.js');
  const redacted = (msg) => E2.shapeError({ execution: { id: 1, error: { message: msg } } }, {}).error;
  assert.equal(redacted('INVALID_WEBHOOK_SECRET: the shared secret does not match'), 'INVALID_WEBHOOK_SECRET: the shared secret does not match');
  assert.match(redacted('client_secret=abc123def456 failed'), /client_secret=\[redacted\]/);
  assert.match(redacted('x-webhook-secret: s3cr3t'), /x-webhook-secret: \[redacted\]/);
  assert.match(redacted('api_key="k-12345678" rejected'), /api_key="\[redacted\]/);
  assert.match(redacted('Authorization: Bearer abcdefghijklmnop'), /\[redacted\]/);
  assert.doesNotMatch(redacted('token expired for password reset flow'), /redacted/);
});
