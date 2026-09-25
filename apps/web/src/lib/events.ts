import {
  AlarmClock,
  BadgeCheck,
  Bell,
  BellRing,
  CalendarCheck,
  CalendarPlus,
  CircleCheck,
  CircleDashed,
  CircleX,
  Forward,
  GitMerge,
  Hourglass,
  Mail,
  MailCheck,
  MailWarning,
  Pencil,
  RotateCcw,
  ScanText,
  Send,
  ShieldAlert,
  Snowflake,
  TriangleAlert,
  Undo2,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import type { Tone } from "./labels";

export interface EventStyle {
  icon: LucideIcon;
  tone: Tone;
  /** "effect" entries hang off the step that caused them (a notification sent because a reminder came due). */
  role: "cause" | "effect";
}

const cause = (icon: LucideIcon, tone: Tone = "neutral"): EventStyle => ({ icon, tone, role: "cause" });
const effect = (icon: LucideIcon, tone: Tone = "neutral"): EventStyle => ({ icon, tone, role: "effect" });

const STYLES: Record<string, EventStyle> = {
  MESSAGE_RECEIVED: cause(Mail),
  AI_CLASSIFIED: effect(ScanText, "info"),
  COMMITMENT_DETECTED: cause(ScanText, "info"),
  EXTRACTION_FAILED: cause(TriangleAlert, "danger"),
  OBLIGATION_CREATED: cause(CircleDashed, "info"),
  CANDIDATE_STORED: cause(CircleDashed),
  CANDIDATE_PROMOTED: cause(CircleCheck, "ok"),
  CANDIDATE_DISCARDED: cause(CircleX),
  DUPLICATE_MERGED: cause(GitMerge, "info"),
  NOT_AN_OBLIGATION: cause(CircleX),
  OBLIGATION_UPDATED: cause(Pencil),
  STATUS_CHANGED: cause(Workflow),
  ACCEPTED: cause(CircleCheck, "ok"),
  SNOOZED: cause(Snowflake, "info"),
  COMPLETED: cause(BadgeCheck, "ok"),
  DISMISSED: cause(CircleX),
  REOPENED: cause(Undo2),
  OVERDUE_MARKED: cause(AlarmClock, "danger"),
  ESCALATED: cause(ShieldAlert, "danger"),
  RECURRENCE_SPAWNED: cause(RotateCcw, "info"),
  NOTIFICATION_QUEUED: effect(Bell),
  NOTIFICATION_SENT: effect(MailCheck, "ok"),
  NOTIFICATION_FAILED: effect(MailWarning, "danger"),
  NOTIFICATION_SUPPRESSED: effect(Bell),
  REMINDERS_STOPPED: cause(BellRing, "ok"),
  APPROVAL_REQUESTED: cause(Hourglass, "warn"),
  APPROVAL_APPROVED: cause(CircleCheck, "ok"),
  APPROVAL_REJECTED: cause(CircleX),
  APPROVAL_EXPIRED: cause(Hourglass),
  ACTION_EXECUTED: effect(Send, "ok"),
  ACTION_FAILED: effect(TriangleAlert, "danger"),
  CALENDAR_CHECKED: effect(CalendarCheck),
  CALENDAR_EVENT_CREATED: cause(CalendarPlus, "ok"),
  FOLLOW_UP_DRAFTED: cause(Forward, "info"),
  AUTOMATION_RUN: effect(Workflow),
  SECURITY: cause(ShieldAlert),
};

export function eventStyle(eventType: string, planned = false): EventStyle {
  if (planned || eventType.startsWith("PLANNED_")) return { icon: AlarmClock, tone: "neutral", role: "cause" };
  return STYLES[eventType] ?? cause(Workflow);
}

/** Who did it, in words a person would use. */
export function actorLabel(actor: string | null | undefined): string {
  if (!actor) return "CommitmentOS";
  const [type, id] = [actor.split(":")[0] ?? "", actor.split(":").slice(1).join(":")];
  switch (type) {
    case "user":
      return "You";
    case "ai":
      return id ? `Language model · ${id}` : "Language model";
    case "n8n":
      return "Automation";
    case "system":
      return id ? `CommitmentOS · ${id}` : "CommitmentOS";
    default:
      return "CommitmentOS";
  }
}
