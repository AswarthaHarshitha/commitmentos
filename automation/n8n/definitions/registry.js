'use strict';
// One place for workflow identities. Ids are fixed so workflows can call each other and be referenced from error settings.
module.exports = {
  'error-handler': { id: 'cosWfErrorHandler01', name: 'Error Handler' },
  'notification-dispatcher': { id: 'cosWfDispatch00001', name: 'Notification Dispatcher' },
  'incoming-detection': { id: 'cosWfIncoming00001', name: 'Commitment Detection' },
  'deadline-monitor': { id: 'cosWfMonitor000001', name: 'Deadline Monitor' },
  'approved-actions': { id: 'cosWfActions000001', name: 'Approved Actions' },
  'calendar-sync': { id: 'cosWfCalendar00001', name: 'Calendar Synchronization' },
  'follow-up-assistant': { id: 'cosWfFollowUp00001', name: 'Follow-up Assistant' },
  'completion-detection': { id: 'cosWfCompletion001', name: 'Completion Detection' },
};
