"use client";

import { CalendarPlus, Mail, Pencil } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { EditApprovalDialog } from "@/components/edit-approval-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { errorMessage, type Approval } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { actorLabel } from "@/lib/events";
import { APPROVAL_LABEL, type Tone } from "@/lib/labels";
import { useApprovalDecision } from "@/lib/queries";
import { formatDuration, formatMoment, formatRange, timeAgo } from "@/lib/time";

const str = (payload: Approval["payload"], key: string) => (typeof payload[key] === "string" ? (payload[key] as string) : "");

const APPROVAL_STATUS: Record<Approval["status"], { label: string; tone: Tone }> = {
  PENDING: { label: "Waiting for you", tone: "warn" },
  APPROVED: { label: "Approved, queued", tone: "info" },
  EXECUTING: { label: "In progress", tone: "info" },
  EXECUTED: { label: "Done", tone: "ok" },
  FAILED: { label: "Failed", tone: "danger" },
  REJECTED: { label: "Declined", tone: "neutral" },
  EXPIRED: { label: "Expired", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

/** What exactly will happen, spelled out - a person approves the thing itself, not a description of it. */
function Preview({ approval }: { approval: Approval }) {
  const { now, timeZone } = useClock();
  const p = approval.payload;
  if (approval.action_type === "SEND_FOLLOW_UP") {
    return (
      <div className="mt-3 rounded-md border border-line bg-surface-2/60 p-4 text-[14px]">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt className="text-text-3">To</dt>
          <dd className="break-all text-ink">{str(p, "to")}</dd>
          <dt className="text-text-3">Subject</dt>
          <dd className="text-ink">{str(p, "subject")}</dd>
        </dl>
        <p className="mt-3 whitespace-pre-line border-t border-line pt-3 leading-6 text-text">{str(p, "body")}</p>
      </div>
    );
  }
  if (approval.action_type === "CREATE_CALENDAR_EVENT") {
    const zone = str(p, "timezone") || timeZone;
    return (
      <div className="mt-3 rounded-md border border-line bg-surface-2/60 p-4 text-[14px]">
        <p className="font-medium text-ink">{str(p, "title")}</p>
        <p data-numeric className="mt-1 text-text">
          {str(p, "start_at") && str(p, "end_at") ? formatRange(str(p, "start_at"), str(p, "end_at"), zone, now) : str(p, "start_at") && formatMoment(str(p, "start_at"), zone, now)}
        </p>
        {str(p, "location") && <p className="mt-1 text-text-2">{str(p, "location")}</p>}
      </div>
    );
  }
  return null;
}

/** A proposed action waiting for a decision. Nothing outside CommitmentOS happens until it is approved. */
export function ApprovalItem({ approval }: { approval: Approval }) {
  const { now, timeZone, status: system } = useClock();
  const toast = useToast();
  const decide = useApprovalDecision();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const followUp = approval.action_type === "SEND_FOLLOW_UP";
  const editable = followUp || approval.action_type === "CREATE_CALENDAR_EVENT";
  const Icon = followUp ? Mail : CalendarPlus;

  async function run(decision: "approve" | "reject") {
    setBusy(decision);
    try {
      await decide.mutateAsync({ id: approval.id, decision });
      toast(
        decision === "approve"
          ? { tone: "ok", title: "Approved", description: followUp ? (system?.email_delivery === "local_test_inbox" ? "It will land in the local test inbox, not the real address: email delivery is not set up." : "The email will be sent in a moment.") : "It will be added to your calendar in a moment." }
          : { title: "Declined", description: "Nothing was sent or changed." },
      );
    } catch (error) {
      toast({ tone: "danger", title: decision === "approve" ? "Could not approve it" : "Could not decline it", description: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  const expires = approval.expires_at ? new Date(approval.expires_at).getTime() - now.getTime() : null;

  return (
    <li className="px-4 py-5 sm:px-5" data-testid="approval-item">
      <p className="flex flex-wrap items-center gap-x-2 text-[12.5px] text-text-2">
        <Icon aria-hidden className="size-3.5" strokeWidth={1.8} />
        <span>{APPROVAL_LABEL[approval.action_type]}</span>
        <span aria-hidden>·</span>
        <span>Suggested by {actorLabel(approval.proposed_by)}</span>
        <span aria-hidden>·</span>
        <span>{timeAgo(approval.created_at, now, timeZone)}</span>
      </p>
      <h3 className="mt-1.5 text-[16px] font-medium leading-6 text-ink">{approval.title}</h3>
      {approval.rationale && <p className="mt-0.5 text-[13.5px] text-text-2">{approval.rationale}</p>}
      <Preview approval={approval} />
      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" loading={busy === "approve"} disabled={busy !== null} onClick={() => run("approve")}>
          {followUp ? "Approve and send" : approval.action_type === "CREATE_CALENDAR_EVENT" ? "Add to calendar" : "Approve"}
        </Button>
        {editable && (
          <Button size="sm" icon={<Pencil aria-hidden className="size-3.5" strokeWidth={1.8} />} disabled={busy !== null} onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
        <Button size="sm" variant="ghost" loading={busy === "reject"} disabled={busy !== null} onClick={() => run("reject")}>
          Not now
        </Button>
        <Link href={`/obligations/${approval.obligation_id}`} className="ml-auto text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
          View commitment
        </Link>
      </div>
      {expires !== null && expires > 0 && <p className="mt-2 text-[12.5px] text-text-3">Expires in {formatDuration(expires)} if you do nothing.</p>}
      <EditApprovalDialog approval={editing ? approval : null} onOpenChange={setEditing} />
    </li>
  );
}

/** A finished decision, one quiet line: what it was, how it ended, and (for a failure) why. */
export function DecidedApproval({ approval }: { approval: Approval }) {
  const { now, timeZone, status: system } = useClock();
  // an email "sent" into the local test inbox reached nobody: say so instead of a reassuring "Done"
  const localOnly = approval.status === "EXECUTED" && approval.action_type === "SEND_FOLLOW_UP" && system?.email_delivery === "local_test_inbox";
  const status = localOnly ? { label: "Local inbox only", tone: "warn" as Tone } : APPROVAL_STATUS[approval.status];
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-3 sm:px-5">
      <Badge tone={status.tone} dot>
        {status.label}
      </Badge>
      <Link href={`/obligations/${approval.obligation_id}`} className="text-[14px] text-ink underline-offset-4 hover:underline">
        {approval.title}
      </Link>
      <span className="text-[13px] text-text-3">{timeAgo(approval.updated_at, now, timeZone)}</span>
      {approval.status === "FAILED" && approval.error && <p className="basis-full text-[13px] text-danger">{approval.error}</p>}
      {localOnly && <p className="basis-full text-[13px] text-text-2">Placed in the local test inbox. It did not reach {String(approval.payload.to ?? "the recipient")}, because email delivery is not set up.</p>}
    </li>
  );
}
