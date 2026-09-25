"use client";

import { ArrowLeft, FileSearch, Forward, MoreHorizontal, Pencil, RotateCcw, Snowflake, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { TypeIcon } from "@/components/commitment-card";
import { CalendarCard, ConfidenceMeter, DetailsCard, Fact, RemindersCard, RunsCard } from "@/components/detail/parts";
import { TimelineView } from "@/components/detail/timeline-view";
import { ObligationDialog } from "@/components/edit-obligation-dialog";
import { ErrorPanel } from "@/components/error-panel";
import { ApprovalItem } from "@/components/inbox/approval-item";
import { SourceQuote, WhyDetected } from "@/components/understanding";
import { StatusIndicator } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { CompletionCheck } from "@/components/ui/completion-check";
import { DeadlineIndicator } from "@/components/ui/deadline-indicator";
import { Modal } from "@/components/ui/dialog";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useCompleteWithUndo, useDecisions } from "@/lib/actions";
import { ApiError, errorMessage, type ObligationDetail, type Priority } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useClock } from "@/lib/clock";
import { contextLine } from "@/lib/context";
import { PRIORITY_LABEL, STATUS, TYPE_LABEL } from "@/lib/labels";
import { useObligation, useObligationAction, useTimeline } from "@/lib/queries";
import { useSettling } from "@/lib/settling";
import { addLocalDays, describeDeadline, formatDateTime, formatWeekdayDay, zonedTimeToUtc } from "@/lib/time";

function DetailSkeleton() {
  return (
    <div aria-busy="true" role="status" aria-label="Opening this commitment">
      <Skeleton className="mb-6 h-4 w-20" />
      <Skeleton className="mb-3 h-9 w-3/5" />
      <Skeleton className="mb-8 h-5 w-1/3" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  const router = useRouter();
  return (
    <button
      type="button"
      onClick={() => (window.history.length > 1 ? router.back() : router.push("/overview"))}
      className="-ml-1.5 mb-5 inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[13.5px] font-medium text-text-2 hover:bg-surface-2 hover:text-ink"
    >
      <ArrowLeft aria-hidden className="size-4" strokeWidth={1.8} />
      Back
    </button>
  );
}

const PRIORITY_TONE: Record<Priority, string> = { URGENT: "text-danger", HIGH: "text-warn", MEDIUM: "text-text-2", LOW: "text-text-3" };

export default function ObligationPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useObligation(id);
  const timeline = useTimeline(id);

  if (detail.isPending) return <DetailSkeleton />;
  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return (
      <>
        <BackLink />
        <Card>
          <EmptyState
            icon={FileSearch}
            title="This commitment could not be found"
            description="It may have been removed, or the link may belong to another account."
            action={
              <Link href="/overview" className="inline-flex h-9 items-center rounded-md border border-line-strong bg-surface px-3.5 text-[14px] font-medium text-ink hover:bg-surface-2">
                Go to Overview
              </Link>
            }
          />
        </Card>
      </>
    );
  }
  if (detail.error || !detail.data) return <ErrorPanel error={detail.error} onRetry={() => void detail.refetch()} title="Couldn't open this commitment" />;
  return <Detail data={detail.data} timeline={timeline} />;
}

function Detail({ data, timeline }: { data: ObligationDetail; timeline: ReturnType<typeof useTimeline> }) {
  const { now, timeZone } = useClock();
  const toast = useToast();
  const ob = data.obligation;
  const { complete, busyId } = useCompleteWithUndo();
  const { accept, dismiss } = useDecisions();
  const snooze = useObligationAction("snooze");
  const reopen = useObligationAction("reopen");
  const followUp = useObligationAction("follow-up");
  const schedule = useObligationAction("schedule");
  const [editing, setEditing] = useState(false);
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

  const settling = useSettling(ob.id);
  const done = ob.status === "COMPLETED" || settling;
  const closed = done || ob.status === "DISMISSED";
  const reviewing = ob.status === "NEEDS_REVIEW" || ob.status === "DETECTED";
  const status = STATUS[ob.status];
  const due = describeDeadline({ dueAt: ob.due_at, precision: ob.due_precision, timeZone, now });
  const when = ob.due_at
    ? ob.due_precision === "DATE"
      ? `${formatWeekdayDay(new Date(ob.due_at), timeZone, now)}, end of day`
      : formatDateTime(ob.due_at, timeZone, now)
    : null;
  const primary = data.sources.find((s) => s.role === "PRIMARY") ?? data.sources[0];
  const others = data.sources.filter((s) => s !== primary);
  const waitingApprovals = data.approvals.filter((a) => a.status === "PENDING");

  async function run(label: string, work: () => Promise<void>, failure: string) {
    setPending(label);
    try {
      await work();
    } catch (error) {
      toast({ tone: "danger", title: failure, description: errorMessage(error) });
    } finally {
      setPending(null);
    }
  }

  const snoozeTo = (label: string, body: Record<string, unknown>) =>
    run("snooze", async () => {
      await snooze.mutateAsync({ id: ob.id, body });
      toast({ tone: "ok", title: "Snoozed", description: `Reminders are paused until ${label}.` });
    }, "Could not snooze it");

  const morning = (days: number) => zonedTimeToUtc(addLocalDays(now, timeZone, days), "09:00", timeZone);
  const tomorrowMorning = morning(1);
  const nextWeek = morning(7);
  const menu = [
    { label: "For 1 hour", onSelect: () => void snoozeTo("an hour from now", { hours: 1 }) },
    { label: "For 3 hours", onSelect: () => void snoozeTo("three hours from now", { hours: 3 }) },
    { label: "Until tomorrow morning", onSelect: () => void snoozeTo(`tomorrow at ${formatDateTime(tomorrowMorning.toISOString(), timeZone, now).split(" at ")[1]}`, { until: tomorrowMorning.toISOString() }) },
    { label: "Until next week", onSelect: () => void snoozeTo(formatWeekdayDay(nextWeek, timeZone, now), { until: nextWeek.toISOString() }) },
  ];

  const startFollowUp = () => {
    if (!ob.counterparty_email) {
      toast({ title: "Add their email first", description: "A follow-up needs someone to send it to." });
      setEditing(true);
      return;
    }
    void run("follow-up", async () => {
      await followUp.mutateAsync({ id: ob.id });
      toast({ tone: "ok", title: "Draft ready", description: "Review it below. Nothing is sent until you approve." });
    }, "Could not draft a follow-up");
  };

  const addToCalendar = () =>
    run("schedule", async () => {
      await schedule.mutateAsync({ id: ob.id });
      toast({ tone: "ok", title: "Adding it to your calendar", description: "It will appear in the timeline once done." });
    }, "Could not add it to the calendar");

  const suggestion = data.suggested_next_action;
  const suggestionButton: { label: string; onClick: () => void } | null = closed
    ? null
    : (
        {
          REVIEW: { label: "Accept", onClick: () => void accept(ob) },
          RESOLVE_OVERDUE: { label: "Mark complete", onClick: () => void complete(ob) },
          DO_IT_NOW: { label: "Mark complete", onClick: () => void complete(ob) },
          CONFIRM_WITH_SENDER: { label: "Draft a reply", onClick: startFollowUp },
          ADD_TO_CALENDAR: { label: "Add to my calendar", onClick: () => void addToCalendar() },
          SET_DEADLINE: { label: "Set a deadline", onClick: () => setEditing(true) },
        } as Record<string, { label: string; onClick: () => void }>
      )[suggestion.code] ?? null;

  return (
    <>
      <BackLink />

      <header className="mb-8">
        <p className="flex flex-wrap items-center gap-x-2.5 text-[13px] text-text-2">
          <TypeIcon type={ob.obligation_type} />
          <span>{TYPE_LABEL[ob.obligation_type]}</span>
          <span aria-hidden>·</span>
          <StatusIndicator tone={done ? "ok" : status.tone} label={done ? "Completed" : status.label} />
          <span aria-hidden>·</span>
          <span className={cn("font-medium", PRIORITY_TONE[ob.priority])}>{PRIORITY_LABEL[ob.priority]} priority</span>
        </p>
        <div className="mt-2.5 flex items-start gap-3.5">
          {!closed || done ? (
            <div className="pt-1.5">
              <CompletionCheck checked={done} disabled={done || closed} busy={busyId === ob.id} label={done ? "This commitment is complete" : `Mark "${ob.title}" complete`} onToggle={() => void complete(ob)} />
            </div>
          ) : null}
          <div className="min-w-0">
            <h1 className={cn("text-[26px] font-semibold leading-8 tracking-[-0.02em] transition-colors duration-500 md:text-[32px] md:leading-10", closed ? "text-text-2 line-through decoration-line-strong" : "text-ink")}>{ob.title}</h1>
            <p className="mt-1 text-[15px] text-text-2">{contextLine(ob)}</p>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1 pl-0 md:pl-[36px]">
          <DeadlineIndicator info={done ? { label: "Completed", detail: ob.completed_at ? formatDateTime(ob.completed_at, timeZone, now) : null, tone: "done" } : due} size="lg" />
        </div>

        <div className="mt-6 flex flex-wrap items-center gap-2 md:pl-[36px]">
          {reviewing && (
            <Button variant="primary" loading={pending === "accept"} onClick={() => void run("accept", async () => void (await accept(ob)), "Could not accept it")}>
              Accept
            </Button>
          )}
          {!closed && !reviewing && (
            <Button variant="primary" loading={busyId === ob.id} onClick={() => void complete(ob)}>
              Mark complete
            </Button>
          )}
          {!closed && !reviewing && (
            <DropdownMenu label="Snooze options" trigger={<Button icon={<Snowflake aria-hidden className="size-4" strokeWidth={1.7} />} loading={pending === "snooze"}>Snooze</Button>} actions={menu} align="start" />
          )}
          {!closed && (
            <Button icon={<Pencil aria-hidden className="size-4" strokeWidth={1.7} />} onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {!closed && !reviewing && (
            <Button icon={<Forward aria-hidden className="size-4" strokeWidth={1.7} />} loading={pending === "follow-up"} onClick={startFollowUp}>
              Follow up
            </Button>
          )}
          {closed && (
            <Button
              icon={<RotateCcw aria-hidden className="size-4" strokeWidth={1.7} />}
              loading={pending === "reopen"}
              onClick={() =>
                void run("reopen", async () => {
                  await reopen.mutateAsync({ id: ob.id });
                  toast({ tone: "ok", title: "Reopened", description: "Its reminders are back on." });
                }, "Could not reopen it")
              }
            >
              Reopen
            </Button>
          )}
          {!closed && (
            <DropdownMenu
              label="More actions"
              trigger={
                <Button variant="ghost" aria-label="More actions" icon={<MoreHorizontal aria-hidden className="size-4" />} />
              }
              actions={[{ label: "Dismiss", icon: X, tone: "danger", onSelect: () => setConfirmDismiss(true) }]}
            />
          )}
        </div>
        {!closed && !reviewing && (
          <p className="mt-3.5 text-[13px] text-text-2 md:pl-[36px]">
            {data.follow_up_to ? (
              <>
                Follow-ups go to <span className="font-medium text-ink">{data.follow_up_to}</span>
                {data.follow_up_to_original ? ", the address the original email came from." : "."}
              </>
            ) : (
              "There is no address to follow up with yet. Add one with Edit."
            )}
          </p>
        )}
      </header>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          {waitingApprovals.length > 0 && (
            <Card className="border-warn-line">
              <CardHeader title="Waiting for your decision" description="Nothing happens until you approve it." />
              <ul className="divide-y divide-line">
                {waitingApprovals.map((a) => (
                  <ApprovalItem key={a.id} approval={a} />
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <CardHeader title="What CommitmentOS understood" description={ob.source === "MANUAL" ? "You added this yourself." : "Read from the message it came from."} />
            <dl className="divide-y divide-line px-5">
              <Fact label="The commitment">{data.understanding.action || ob.title}</Fact>
              <Fact label="Deadline">{when ?? <span className="text-text-2">None found</span>}</Fact>
              {(ob.counterparty_name || ob.counterparty_email) && (
                <Fact label="Involves">
                  {ob.counterparty_name}
                  {ob.counterparty_name && ob.counterparty_email && <span className="text-text-2"> · </span>}
                  {ob.counterparty_email}
                </Fact>
              )}
              {ob.source !== "MANUAL" && (
                <Fact label="Confidence">
                  <ConfidenceMeter value={data.understanding.confidence} />
                </Fact>
              )}
            </dl>
            <div className="border-t border-line px-5 py-4">
              <h3 className="mb-2 text-[13px] font-semibold text-ink">Why this was detected</h3>
              <WhyDetected
                explanation={data.understanding.explanation}
                deadlineText={data.understanding.deadline_text}
                deadlineExplanation={data.understanding.deadline_explanation}
                notes={data.understanding.confidence_notes}
                ambiguity={data.understanding.ambiguity}
              />
              {data.understanding.alternatives.length > 0 && (
                <p className="mt-3 text-[13.5px] text-text-2">Other readings considered: {data.understanding.alternatives.join("; ")}</p>
              )}
              {data.understanding.detector && <p className="mt-3 text-[12.5px] text-text-3">Read by {data.understanding.detector}. The deadline was calculated by CommitmentOS, not by the model.</p>}
            </div>
          </Card>

          {primary && (
            <Card>
              <CardHeader title="Original message" description="The words this was based on, unedited." />
              <div className="space-y-4 p-5">
                <SourceQuote now={now} timeZone={timeZone} source={{ sender: primary.sender_name || primary.sender_email, subject: primary.subject, excerpt: primary.excerpt, receivedAt: primary.received_at }} />
                {others.map((s) => (
                  <div key={s.id}>
                    <p className="mb-1.5 text-[13px] text-text-2">{s.role === "DUPLICATE" ? "The same request, seen again" : "Related message"}</p>
                    <SourceQuote now={now} timeZone={timeZone} source={{ sender: s.sender_name || s.sender_email, subject: s.subject, excerpt: s.excerpt, receivedAt: s.received_at }} />
                  </div>
                ))}
              </div>
            </Card>
          )}

          <Card>
            <CardHeader
              title="Timeline"
              description={`What happened, and what is planned. Times are in ${timeZone.replace(/_/g, " ")}.`}
              action={
                <Link href={`/activity?obligation=${ob.id}`} className="text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
                  Full audit trail
                </Link>
              }
            />
            <div className="px-5 py-5">
              {timeline.isPending ? (
                <div role="status" aria-label="Loading the timeline" className="space-y-4">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-4/5" />
                  <Skeleton className="h-10 w-3/5" />
                </div>
              ) : timeline.error || !timeline.data ? (
                <ErrorPanel error={timeline.error} onRetry={() => void timeline.refetch()} title="Couldn't load the timeline" />
              ) : (
                <TimelineView entries={timeline.data} />
              )}
            </div>
          </Card>
        </div>

        <aside aria-label="Next steps and related activity" className="min-w-0 space-y-6">
          <Card className={cn(!closed && suggestionButton && "border-line-strong")}>
            <CardHeader title="Suggested next step" />
            <div className="px-5 py-4">
              <p className="text-[15px] font-medium text-ink">{suggestion.label}</p>
              <p className="mt-1 text-[13.5px] leading-5 text-text-2">{suggestion.reason}</p>
              {suggestionButton && (
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button variant="primary" size="sm" onClick={suggestionButton.onClick}>
                    {suggestionButton.label}
                  </Button>
                  {suggestion.code === "RESOLVE_OVERDUE" && (
                    <Button size="sm" onClick={() => setEditing(true)}>
                      Move the deadline
                    </Button>
                  )}
                </div>
              )}
            </div>
          </Card>
          <CalendarCard events={data.calendar_events} timeZone={timeZone} now={now} />
          <RemindersCard items={data.notifications} now={now} timeZone={timeZone} />
          <RunsCard runs={data.runs} now={now} timeZone={timeZone} />
          <DetailsCard ob={ob} timeZone={timeZone} now={now} />
        </aside>
      </div>

      <ObligationDialog open={editing} onOpenChange={setEditing} ob={ob} emailLocked={data.follow_up_to_original} />
      <Modal
        open={confirmDismiss}
        onOpenChange={setConfirmDismiss}
        title="Dismiss this commitment?"
        description="It will stop reminding you and move to Completed, under Dismissed. You can reopen it later."
        footer={
          <>
            <Button onClick={() => setConfirmDismiss(false)}>Keep it</Button>
            <Button
              variant="primary"
              onClick={() => {
                setConfirmDismiss(false);
                void dismiss(ob);
              }}
            >
              Dismiss
            </Button>
          </>
        }
      />
    </>
  );
}
