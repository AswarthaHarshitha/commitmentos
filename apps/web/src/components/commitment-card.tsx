"use client";

import { motion } from "framer-motion";
import { AlarmClock, ArrowRight, CalendarDays, FileText, Handshake, Hourglass, ListChecks, RefreshCw, Receipt, Undo2, Users, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { CompletionCheck } from "@/components/ui/completion-check";
import { DeadlineIndicator } from "@/components/ui/deadline-indicator";
import { StatusIndicator } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { useCompleteWithUndo } from "@/lib/actions";
import type { Obligation, ObligationType } from "@/lib/api";
import { contextLine, nextStep } from "@/lib/context";
import { PRIORITY_LABEL, STATUS, TYPE_LABEL } from "@/lib/labels";
import { useSettling } from "@/lib/settling";
import { describeDeadline } from "@/lib/time";
import { cn } from "@/lib/cn";

const TYPE_ICON: Record<ObligationType, LucideIcon> = {
  DEADLINE: AlarmClock,
  PAYMENT: Receipt,
  APPOINTMENT: CalendarDays,
  INTERVIEW: Users,
  DOCUMENT_REQUEST: FileText,
  FOLLOW_UP: Hourglass,
  RENEWAL: RefreshCw,
  RETURN: Undo2,
  PERSONAL_COMMITMENT: Handshake,
  TASK: ListChecks,
  OTHER: ListChecks,
};

export function TypeIcon({ type, className }: { type: ObligationType; className?: string }) {
  const Icon = TYPE_ICON[type];
  return (
    <Tip label={TYPE_LABEL[type]}>
      <span className={cn("inline-flex", className)}>
        <Icon aria-label={TYPE_LABEL[type]} role="img" className="size-4 text-text-3" strokeWidth={1.6} />
      </span>
    </Tip>
  );
}

interface Props {
  ob: Obligation;
  now: Date;
  timeZone: string;
  variant?: "row" | "focus";
  /** hide the complete control (e.g. on the Completed page) */
  readOnly?: boolean;
}

export function CommitmentCard({ ob, now, timeZone, variant = "row", readOnly = false }: Props) {
  const { complete, busyId } = useCompleteWithUndo();
  // for a moment after ticking, the row stays put and reads as done, so the completion is seen before the list closes the gap
  const settling = useSettling(ob.id);
  const done = ob.status === "COMPLETED" || settling;
  const closed = done || ob.status === "DISMISSED";
  const due = describeDeadline({ dueAt: ob.due_at, precision: ob.due_precision, timeZone, now });
  const status = STATUS[ob.status];
  const next = closed ? null : nextStep(ob);
  const showStatus = !["OPEN", "COMPLETED"].includes(ob.status);
  const prominent = ob.priority === "URGENT" || ob.priority === "HIGH";
  const focus = variant === "focus";

  return (
    <motion.article
      layout="position"
      data-testid="commitment-card"
      data-status={ob.status}
      animate={{ opacity: closed ? 0.66 : 1 }}
      transition={{ duration: 0.3 }}
      className={cn("group flex gap-3.5", focus ? "p-6 sm:p-7" : "px-4 py-4 sm:px-5", !focus && "transition-colors hover:bg-surface-2/70")}
    >
      {!readOnly && !closed ? (
        <div className={cn(focus ? "pt-1" : "pt-0.5")}>
          <CompletionCheck checked={done} busy={busyId === ob.id} label={`Mark "${ob.title}" complete`} onToggle={() => complete(ob)} />
        </div>
      ) : done ? (
        <div className="pt-0.5">
          <CompletionCheck checked disabled label={`"${ob.title}" is complete`} onToggle={() => {}} />
        </div>
      ) : null}

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className={cn("text-ink", focus ? "text-[22px] font-semibold leading-7 tracking-[-0.015em]" : "text-[15px] font-medium leading-6", closed && "text-text-2 line-through decoration-line-strong")}>
              <Link href={`/obligations/${ob.id}`} className="hover:underline decoration-line-strong underline-offset-4">
                {ob.title}
              </Link>
            </h3>
            <p className={cn("mt-0.5 flex flex-wrap items-center gap-x-2 text-text-2", focus ? "text-[14.5px]" : "text-[13.5px]")}>
              <TypeIcon type={ob.obligation_type} />
              <span>{contextLine(ob)}</span>
              {prominent && !closed && (
                <span className={cn("text-[12.5px] font-medium", ob.priority === "URGENT" ? "text-danger" : "text-warn")}>· {PRIORITY_LABEL[ob.priority]} priority</span>
              )}
            </p>
          </div>
          {!focus && (
            <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
              <Link href={`/obligations/${ob.id}`} className="inline-flex h-8 items-center gap-1 rounded-md px-3 text-[13px] font-medium text-text-2 hover:bg-surface-2 hover:text-ink">
                Open
              </Link>
              {!readOnly && !closed && (
                <Button size="sm" variant="secondary" loading={busyId === ob.id} onClick={() => complete(ob)}>
                  Complete
                </Button>
              )}
            </div>
          )}
        </div>

        <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1", focus ? "mt-4" : "mt-2")}>
          <DeadlineIndicator info={done ? { label: "Completed", detail: null, tone: "done" } : due} size={focus ? "lg" : "sm"} />
          {showStatus && <StatusIndicator tone={status.tone} label={status.label} />}
        </div>

        {focus && (
          <div className="mt-5 flex flex-wrap items-center gap-2">
            {!closed && (
              <Button variant="primary" loading={busyId === ob.id} onClick={() => complete(ob)}>
                Mark complete
              </Button>
            )}
            <Link href={`/obligations/${ob.id}`} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3.5 text-[14px] font-medium text-ink hover:bg-surface-2">
              Open <ArrowRight aria-hidden className="size-4" />
            </Link>
            {next && <span className="text-[13.5px] text-text-2">Next: {next}</span>}
          </div>
        )}
        {!focus && next && <p className="mt-1.5 text-[13px] text-text-3">Next: {next}</p>}
      </div>
    </motion.article>
  );
}
