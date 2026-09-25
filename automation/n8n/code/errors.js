// @include _common.js
// Error Handler: shape n8n's Error Trigger payload into a run.failed event for the API's automation history.

function shapeError(payload, workflowKeys = {}, nowMs = Date.now()) {
  const execution = (payload && payload.execution) || {};
  const workflow = (payload && payload.workflow) || {};
  const message = truncate(redact(errorMessage(execution.error || payload)), 1500);
  return {
    event: 'run.failed',
    n8n_execution_id: String(execution.id != null ? execution.id : `err-${nowMs}`),
    workflow_key: workflowKeys[workflow.id] || 'unknown',
    n8n_workflow_id: workflow.id != null ? String(workflow.id) : null,
    workflow_name: workflow.name || null,
    status: 'FAILED',
    error: message,
    error_node: execution.lastNodeExecuted || null,
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { shapeError };
