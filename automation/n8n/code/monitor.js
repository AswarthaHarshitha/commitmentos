// @include _common.js
// Deadline Monitor: decide whether a tick did anything worth recording or delivering.
// (The deadline rules themselves - which reminder is due, overdue, escalation - live in the API, not here.)

function assessTick(tick) {
  const n = (k) => Number((tick && tick[k]) || 0);
  const errors = Array.isArray(tick && tick.errors) ? tick.errors : [];
  const notifying = n('reminders_queued') + n('marked_overdue') + n('escalated') + n('review_nudges');
  const didWork = notifying + n('approvals_expired') + n('stale_detected_swept') + n('notifications_suppressed') + errors.length > 0;
  return {
    did_work: didWork,
    deliver: notifying > 0,
    status: errors.length ? 'PARTIAL' : 'SUCCESS',
    error: errors.length ? truncate(redact(errors.slice(0, 3).join('; ')), 900) : null,
    result: {
      evaluated: n('evaluated'), reminders_queued: n('reminders_queued'), marked_overdue: n('marked_overdue'), escalated: n('escalated'),
      review_nudges: n('review_nudges'), approvals_expired: n('approvals_expired'), suppressed: n('notifications_suppressed'), errors: errors.length,
    },
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { assessTick };
