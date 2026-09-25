'use strict';
const scanWorkflow = require('./scans');

// Which commitments deserve a calendar event is decided by the API (rules, not a model). This workflow proposes them;
// the user approves; the Approved Actions workflow then creates the event.
module.exports = () =>
  scanWorkflow({
    key: 'calendar-sync',
    minutes: 15,
    scheduleName: 'Every 15 minutes',
    webhookPath: 'commitmentos-calendar-scan',
    scanPath: '/api/internal/calendar/scan',
    description: 'Proposes calendar events for commitments (ask, then create).',
  });
