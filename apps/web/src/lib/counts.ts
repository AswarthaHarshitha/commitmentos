"use client";

import { useCandidates, useDashboard } from "./queries";

/** The numbers beside "Inbox" and "Overdue": what is waiting for a person's decision, and what is late. */
export function useNavCounts(): { inbox: number; overdue: number } {
  const { data } = useDashboard();
  const { data: candidates } = useCandidates();
  const inbox = (data?.status_counts.NEEDS_REVIEW ?? 0) + (data?.summary.approvals_pending ?? 0) + (candidates?.length ?? 0);
  return { inbox, overdue: data?.summary.overdue ?? 0 };
}
