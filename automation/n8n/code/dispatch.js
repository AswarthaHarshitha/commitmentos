// @include _common.js
// Notification Dispatcher: hand out claimed notifications, report each outcome, summarise the run.

const claimedItems = (claim) => (claim && Array.isArray(claim.items) ? claim.items : []);

// one report per notification, built from the delivery outcome (an error item still carries the original notification)
function deliveryReport(item, outcome, executionId) {
  const base = { notification_id: item.id, n8n_execution_id: String(executionId) };
  if (outcome === 'sent') return { event: 'notification.sent', ...base };
  return { event: 'notification.failed', ...base, error: truncate(redact(errorMessage(item.error || item)), 900) };
}

function summariseDispatch(outcomes) {
  const sent = outcomes.filter((o) => o.event === 'notification.sent').length;
  const failed = outcomes.filter((o) => o.event === 'notification.failed');
  return {
    total: outcomes.length,
    sent,
    failed: failed.length,
    errors: failed.slice(0, 5).map((o) => o.error),
    status: failed.length === 0 ? 'SUCCESS' : sent === 0 ? 'FAILED' : 'PARTIAL',
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { claimedItems, deliveryReport, summariseDispatch };
