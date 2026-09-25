'use strict';
const scanWorkflow = require('./scans');

// Who deserves a nudge is decided by the API (overdue, waiting on someone, cooldown). The draft is a template, never a
// model's words in the user's name. The user reviews and approves; only then is anything sent.
module.exports = () =>
  scanWorkflow({
    key: 'follow-up-assistant',
    minutes: 60,
    scheduleName: 'Every hour',
    webhookPath: 'commitmentos-followup-scan',
    scanPath: '/api/internal/followups/scan',
    description: 'Drafts follow-up emails for review (never sends by itself).',
  });
