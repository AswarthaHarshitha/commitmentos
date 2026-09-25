'use strict';
const { Workflow, webhookTrigger, api, report, ifTrue, code, runSubWorkflow, node } = require('../lib/wf');
const { codeNode, markStart, runStartedBody } = require('./common');
const registry = require('./registry');

const KEY = 'incoming-detection';
const P = "$('Normalize message').first().json"; // the normalised message + owner, the spine of this workflow

// Gmail -> normalise -> clean -> AI -> validate -> confidence -> dedupe -> API -> notify.
// n8n owns the orchestration (trigger, retries, branching, hand-offs); the API owns every decision that matters
// (validation, thresholds, deadline arithmetic, deduplication, persistence). The LLM only extracts.
module.exports = function build() {
  const { id, name } = registry[KEY];
  const wf = new Workflow({ id, key: KEY, name, errorWorkflowId: registry['error-handler'].id, description: 'Turns an incoming message into a tracked commitment.' });

  // ---- triggers -------------------------------------------------------------------------------------------------------
  const ingest = wf.add(webhookTrigger('Ingest (webhook)', 'commitmentos-ingest'), [0, 0]);
  const gmail = wf.add(
    node('Gmail Trigger', 'n8n-nodes-base.gmailTrigger', 1.4, { pollTimes: { item: [{ mode: 'everyMinute' }] }, simple: true, filters: { labelIds: ['INBOX'] }, options: {} }, {
      disabled: true,
      notes: 'NOT VERIFIED - needs a Google OAuth credential (connect it in n8n, then enable this node).',
      notesInFlow: true,
    }),
    [0, 2],
  );
  const owner = wf.add(
    code('Mailbox owner', "return $input.all().map((i) => ({ json: { ...i.json, owner_email: 'you@example.com' } }));", {
      notes: 'Set owner_email to the CommitmentOS account that owns this Gmail mailbox.',
    }),
    [1, 2],
  );
  wf.link(gmail, owner);

  const start = wf.add(markStart(), [2, 1]);
  const normalize = wf.add(codeNode('Normalize message', 'normalize-message.js', "return $input.all().map(({ json }) => {\n  let owner = null;\n  try { owner = $('Mailbox owner').first().json.owner_email; } catch (e) { owner = null; }\n  return { json: normalizeIncoming(json, { ownerEmail: owner }) };\n});"), [3, 1]);
  wf.link(ingest, start);
  wf.link(owner, start);
  wf.link(start, normalize);

  // ---- bookkeeping + duplicate check (before paying for an LLM call) ---------------------------------------------------
  const started = wf.add(
    report('Run started', runStartedBody(KEY, `${P}.trigger`, { userEmail: `${P}.user_email`, correlation: `${P}.correlation_id` }), { retries: 3, bestEffort: true }),
    [4, 1],
  );
  const seenCheck = wf.add(
    api('Already processed?', 'POST', '/api/internal/messages/check', {
      body: `{ user_email: ${P}.user_email, source_type: ${P}.message.source_type, external_id: ${P}.message.external_id }`,
    }),
    [5, 1],
  );
  const seen = wf.add(ifTrue('Seen before?', '{{ $json.seen }}'), [6, 1]);
  const skipped = wf.add(codeNode('Skipped duplicate', 'ingestion.js', `return [{ json: skippedDuplicate($json, ${P}) }];`), [7, 3]);
  wf.chain(normalize, started, seenCheck, seen);
  wf.link(seen, skipped, 0);

  // ---- AI extraction (validated by the API, never trusted here) ---------------------------------------------------------
  const extract = wf.add(
    api('Extract (LLM)', 'POST', '/api/internal/extract', { body: `{ user_email: ${P}.user_email, message: ${P}.message }`, retries: 3, waitMs: 3000, errorOutput: true, timeoutMs: 240000 }),
    [7, 0],
  );
  wf.link(seen, extract, 1);
  const okReport = wf.add(codeNode('Build report', 'ingestion.js', `return [{ json: buildExtractionReport($input.first().json, ${P}, $execution.id) }];`), [8, -1]);
  const failReport = wf.add(codeNode('Build failure report', 'ingestion.js', `return [{ json: buildFailureReport($input.first().json, ${P}, $execution.id) }];`), [8, 1]);
  wf.link(extract, okReport, 0);
  wf.link(extract, failReport, 1);

  // ---- hand the proposal to the API, which validates, decides and persists --------------------------------------------
  const record = wf.add(report('Record in CommitmentOS', '$json', { retries: 3 }), [9, 0]);
  wf.link(okReport, record);
  wf.link(failReport, record);
  const summarise = wf.add(
    codeNode(
      'Summarise',
      'ingestion.js',
      `let extract = null;\ntry { extract = $('Extract (LLM)').first().json; } catch (e) { extract = null; }\nreturn [{ json: summariseIngestion($input.first().json, extract && extract.status ? extract : null, ${P}) }];`,
    ),
    [10, 0],
  );
  wf.link(record, summarise);

  // ---- notify (the API queued them; deliver now instead of waiting for the next minute) ---------------------------------
  const notify = wf.add(ifTrue('Notifications queued?', '{{ $json.notifications > 0 }}'), [11, 0]);
  const deliver = wf.add(runSubWorkflow('Deliver notifications', registry['notification-dispatcher'].id, { bestEffort: true }), [12, -1]);
  wf.link(summarise, notify);
  wf.link(notify, deliver, 0);

  // ---- is this message the completion of something already tracked? (asks only when a message was understood) -----------
  const completion = wf.add(ifTrue('Worth a completion check?', "{{ $('Summarise').first().json.check_completion }}"), [13, 0]);
  const prepare = wf.add(code('Prepare completion check', `const p = ${P};\nreturn [{ json: { user_email: p.user_email, message: p.message } }];`), [14, -1]);
  const checkDone = wf.add(runSubWorkflow('Check for completed commitments', registry['completion-detection'].id, { wait: false, bestEffort: true }), [15, -1]);
  wf.link(deliver, completion);
  wf.link(notify, completion, 1);
  wf.link(completion, prepare, 0);
  wf.chain(prepare, checkDone);

  // ---- finish: every path ends here ------------------------------------------------------------------------------------
  const runReport = wf.add(
    codeNode(
      'Build run report',
      'ingestion.js',
      `const last = (name) => { try { return $(name).first().json; } catch (e) { return null; } };\nconst summary = last('Summarise') || last('Skipped duplicate');\nreturn [{ json: runFinishedEvent(summary, ${P}, $execution.id, $workflow.id, $workflow.name, $('Mark start').first().json._t0) }];`,
    ),
    [16, 1],
  );
  wf.link(checkDone, runReport);
  wf.link(completion, runReport, 1);
  wf.link(skipped, runReport);
  const finish = wf.add(report('Report run', '$json', { retries: 3, bestEffort: true }), [17, 1]);
  const result = wf.add(
    code(
      'Result',
      "const last = (name) => { try { return $(name).first().json; } catch (e) { return null; } };\nconst summary = last('Summarise') || last('Skipped duplicate') || {};\nreturn [{ json: { ...summary, n8n_execution_id: String($execution.id) } }];",
    ),
    [18, 1],
  );
  wf.chain(runReport, finish, result);
  return wf;
};
