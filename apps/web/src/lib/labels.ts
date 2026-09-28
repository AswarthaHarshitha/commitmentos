import type { ApprovalAction, ObligationType, Priority, RunStatus, Status } from "./api";

export type Tone = "neutral" | "ok" | "warn" | "danger" | "info";

export const STATUS: Record<Status, { label: string; tone: Tone }> = {
  DETECTED: { label: "Detected", tone: "info" },
  NEEDS_REVIEW: { label: "Needs review", tone: "warn" },
  OPEN: { label: "Open", tone: "neutral" },
  ACTION_REQUIRED: { label: "Action required", tone: "warn" },
  SCHEDULED: { label: "Scheduled", tone: "info" },
  OVERDUE: { label: "Overdue", tone: "danger" },
  ESCALATED: { label: "Escalated", tone: "danger" },
  COMPLETED: { label: "Completed", tone: "ok" },
  DISMISSED: { label: "Dismissed", tone: "neutral" },
};

export const TYPE_LABEL: Record<ObligationType, string> = {
  DEADLINE: "Deadline",
  PAYMENT: "Payment",
  APPOINTMENT: "Appointment",
  INTERVIEW: "Interview",
  DOCUMENT_REQUEST: "Document request",
  FOLLOW_UP: "Follow-up",
  RENEWAL: "Renewal",
  RETURN: "Return",
  PERSONAL_COMMITMENT: "Personal commitment",
  TASK: "Task",
  OTHER: "Other",
};

export const PRIORITY_LABEL: Record<Priority, string> = { URGENT: "Urgent", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };

export const SOURCE_LABEL: Record<string, string> = {
  GMAIL: "Gmail",
  GOOGLE_CALENDAR: "Google Calendar",
  WEBHOOK: "Webhook",
  MANUAL: "Added manually",
  IMPORTED: "Pasted email",
};

export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  RUNNING: { label: "Running", tone: "info" },
  WAITING: { label: "Waiting for you", tone: "warn" },
  SUCCESS: { label: "Succeeded", tone: "ok" },
  FAILED: { label: "Failed", tone: "danger" },
  SKIPPED: { label: "Skipped", tone: "neutral" },
  PARTIAL: { label: "Partly done", tone: "warn" },
};

export const APPROVAL_LABEL: Record<ApprovalAction, string> = {
  SEND_FOLLOW_UP: "Send a follow-up email",
  CREATE_CALENDAR_EVENT: "Add to your calendar",
  DISMISS_OBLIGATION: "Dismiss this commitment",
  COMPLETE_OBLIGATION: "Mark as done",
};

export const TRIGGER_LABEL: Record<string, string> = {
  WEBHOOK: "Webhook",
  SCHEDULE: "Schedule",
  SUBWORKFLOW: "Called by a workflow",
  GMAIL: "Gmail",
  API: "CommitmentOS",
};

export const WORKFLOW_BLURB: Record<string, string> = {
  "incoming-detection": "Reads a message, extracts the commitment and records it.",
  "deadline-monitor": "Checks deadlines and queues the reminders that are due.",
  "calendar-sync": "Proposes calendar events for commitments. Asks before adding.",
  "approved-actions": "Carries out actions you approved.",
  "completion-detection": "Notices when a message says something is done.",
  "follow-up-assistant": "Drafts follow-ups for overdue or awaited commitments.",
  "notification-dispatcher": "Delivers queued reminders by email or Telegram.",
  "api-dispatch": "Hands approved actions to the automation engine.",
};
