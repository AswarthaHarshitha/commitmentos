// @include _common.js
// Incoming Detection: turn the /extract reply (or a failure) into the event the API records, and summarise the outcome.

function buildExtractionReport(extract, prepared, executionId) {
  const ok = extract && extract.status === 'OK';
  return {
    event: 'message.extracted',
    user_email: prepared.user_email,
    message: prepared.message,
    extraction_status: String((extract && extract.status) || 'INVALID_OUTPUT').slice(0, 32),
    extraction_error: ok ? null : truncate(redact((extract && (extract.error_message || extract.error)) || 'the extraction did not complete'), 900),
    extraction: ok ? extract.extraction : null,
    analysis: ok ? extract.analysis : null,
    n8n_execution_id: String(executionId),
  };
}

// the LLM call never produced a reply (unreachable / overloaded / timed out, after n8n's own retries)
function buildFailureReport(failure, prepared, executionId) {
  return buildExtractionReport(
    { status: 'LLM_UNAVAILABLE', error: `The language model could not be reached: ${errorMessage(failure)}` },
    prepared,
    executionId,
  );
}

function summariseIngestion(reply, extract, prepared) {
  const failed = reply.disposition === 'EXTRACTION_FAILED';
  const decision = reply.decision || (extract && extract.decision) || null;
  return {
    outcome: failed ? 'failed' : reply.duplicate_event ? 'duplicate' : 'processed',
    failed,
    disposition: reply.disposition || null,
    decision: decision && decision.action ? decision.action : null,
    obligation_id: reply.obligation_id || null,
    obligation_status: reply.status || null,
    notifications: Number(reply.notifications || 0),
    due_at: reply.due_at || null,
    confidence: reply.confidence == null ? null : reply.confidence,
    error: failed ? truncate(reply.error || reply.status || 'extraction failed', 300) : null,
    external_id: prepared.message.external_id,
    user_email: prepared.user_email,
    // completion detection is only worth asking about when a message was actually understood
    check_completion: !failed && !reply.duplicate_event,
    llm: extract ? { provider: extract.provider || null, model: extract.model || null, attempts: extract.attempts || null, latency_ms: extract.latency_ms || null } : null,
  };
}

function skippedDuplicate(seen, prepared) {
  return {
    outcome: 'duplicate', failed: false, disposition: seen.disposition || null, decision: null,
    obligation_id: seen.obligation_id || null, obligation_status: null, notifications: 0, due_at: null, confidence: null, error: null,
    external_id: prepared.message.external_id, user_email: prepared.user_email, check_completion: false, llm: null,
  };
}

function runFinishedEvent(summary, prepared, executionId, workflowId, workflowName, startedMs) {
  const base = {
    n8n_execution_id: String(executionId),
    workflow_key: 'incoming-detection',
    n8n_workflow_id: String(workflowId),
    workflow_name: workflowName,
    trigger: prepared.trigger,
    user_email: prepared.user_email,
    duration_ms: Math.max(0, Date.now() - startedMs),
    result: { outcome: summary.outcome, disposition: summary.disposition, decision: summary.decision, notifications: summary.notifications, llm: summary.llm },
  };
  if (summary.obligation_id) base.obligation_id = summary.obligation_id;
  if (summary.failed) return { ...base, event: 'run.failed', status: 'FAILED', error: summary.error, error_node: 'Extract (LLM)' };
  return { ...base, event: 'run.finished', status: summary.outcome === 'duplicate' ? 'SKIPPED' : 'SUCCESS' };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildExtractionReport, buildFailureReport, summariseIngestion, skippedDuplicate, runFinishedEvent };
}
