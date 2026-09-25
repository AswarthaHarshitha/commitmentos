"use client";

import { Activity, ArrowUpRight, CircleCheck, FilePlus2, Inbox, Mail, ScanText, Sunrise } from "lucide-react";
import Link from "next/link";
import { CommitmentCard } from "@/components/commitment-card";
import { CommitmentList } from "@/components/commitment-list";
import { ErrorPanel } from "@/components/error-panel";
import { StatLink, StatusBar } from "@/components/insights";
import { SectionLabel } from "@/components/page-header";
import { useQuickAdd } from "@/components/quick-add";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import type { Dashboard, Obligation } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { SOURCE_LABEL, RUN_STATUS } from "@/lib/labels";
import { useDashboard } from "@/lib/queries";
import { formatDay, formatTime, greeting, timeAgo } from "@/lib/time";

function Greeting({ data }: { data: Dashboard }) {
  const { now, timeZone } = useClock();
  const s = data.summary;
  return (
    <header className="mb-7 md:mb-9">
      <p className="eyebrow mb-1.5">
        <time dateTime={now.toISOString()}>
          {new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone }).format(now)}, {formatDay(now, timeZone, now)} · {formatTime(now, timeZone)}
        </time>
      </p>
      <h1 className="text-[28px] font-semibold leading-9 tracking-[-0.022em] md:text-[34px] md:leading-10">
        {greeting(data.part_of_day)}, {data.display_name || "there"}
      </h1>
      <p className={s.tone === "critical" ? "mt-1.5 text-[16px] font-medium text-danger" : s.tone === "attention" ? "mt-1.5 text-[16px] font-medium text-warn" : "mt-1.5 text-[16px] text-text-2"}>
        {s.headline}
      </p>
      {s.subline && <p className="mt-0.5 text-[15px] text-text-2">{s.subline}</p>}
    </header>
  );
}

function Focus({ ob }: { ob: Obligation | null }) {
  const { now, timeZone } = useClock();
  if (!ob) return null; // nothing urgent: the greeting already says so, and "Today" below has its own quiet state
  return (
    <section aria-labelledby="focus-label">
      <p id="focus-label" className="eyebrow mb-2 px-1">
        Start here
      </p>
      <Card className="border-line-strong">
        <CommitmentCard ob={ob} now={now} timeZone={timeZone} variant="focus" />
      </Card>
    </section>
  );
}

function RecentDetections({ items }: { items: Obligation[] }) {
  const { now, timeZone } = useClock();
  return (
    <Card>
      <CardHeader title="Recently detected" description="Found in your messages" action={<ScanText aria-hidden className="size-[18px] text-text-3" strokeWidth={1.6} />} />
      {items.length === 0 ? (
        <p className="px-5 py-6 text-[14px] text-text-2">New commitments show up here as messages arrive.</p>
      ) : (
        <ul className="divide-y divide-line">
          {items.slice(0, 4).map((ob) => (
            <li key={ob.id}>
              <Link href={`/obligations/${ob.id}`} className="block px-5 py-3 hover:bg-surface-2/70">
                <p className="text-[14px] font-medium text-ink">{ob.title}</p>
                <p className="mt-0.5 text-[13px] text-text-2">
                  {SOURCE_LABEL[ob.source] ?? "A message"} · <span data-numeric>{Math.round(ob.confidence * 100)}%</span> confidence · {timeAgo(ob.created_at, now, timeZone)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function AutomationGlance({ data }: { data: Dashboard }) {
  const { now, timeZone } = useClock();
  const a = data.automation;
  const last = a.recent_runs[0];
  return (
    <Card>
      <CardHeader
        title="Automations"
        description="What runs on your behalf"
        action={
          <Link href="/automations" aria-label="Open automations" className="rounded-md p-1 text-text-3 hover:bg-surface-2 hover:text-ink">
            <ArrowUpRight className="size-[18px]" strokeWidth={1.7} />
          </Link>
        }
      />
      <div className="space-y-1 px-4 py-3">
        <StatLink href="/automations" value={a.waiting} label="Waiting for your approval" tone="warn" />
        <StatLink href="/automations" value={a.failed_24h} label="Failed in the last 24 hours" tone="danger" />
      </div>
      {last && (
        <p className="border-t border-line px-5 py-3 text-[13px] text-text-2">
          Last: <span className="font-medium text-ink">{last.workflow_name}</span> · {RUN_STATUS[last.status].label.toLowerCase()} · {timeAgo(last.started_at, now, timeZone)}
        </p>
      )}
    </Card>
  );
}

/** A new account has nothing tracked. Say how to get real commitments in rather than showing a page of empty panels. */
function FirstRun({ name }: { name: string }) {
  const { importEmail, addCommitment } = useQuickAdd();
  return (
    <>
      <header className="mb-7 md:mb-9">
        <h1 className="text-[28px] font-semibold leading-9 tracking-[-0.022em] md:text-[34px] md:leading-10">Welcome{name ? `, ${name}` : ""}</h1>
        <p className="mt-1.5 text-[16px] text-text-2">Nothing is being tracked yet.</p>
      </header>
      <Card className="max-w-2xl">
        <div className="px-6 py-7 sm:px-8">
          <h2 className="text-[18px] font-semibold text-ink">Start with something real</h2>
          <p className="mt-2 text-[15px] leading-6 text-text-2">
            Paste an email that asks something of you. CommitmentOS reads it, works out what is being asked and when it is due, and starts reminding you. Or add a commitment yourself.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button variant="primary" icon={<Mail aria-hidden className="size-4" strokeWidth={1.8} />} onClick={importEmail}>
              Import an email
            </Button>
            <Button icon={<FilePlus2 aria-hidden className="size-4" strokeWidth={1.8} />} onClick={addCommitment}>
              Add a commitment
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}

function Loading() {
  return (
    <div aria-busy="true">
      <Skeleton className="mb-2 h-3.5 w-40" />
      <Skeleton className="mb-3 h-9 w-2/3" />
      <Skeleton className="mb-8 h-5 w-1/3" />
      <Card>
        <SkeletonRows rows={1} />
      </Card>
      <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card>
          <SkeletonRows rows={4} />
        </Card>
        <Card>
          <SkeletonRows rows={3} />
        </Card>
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const { data, error, isPending, refetch } = useDashboard();
  if (isPending) return <Loading />;
  if (error || !data) return <ErrorPanel error={error} onRetry={() => refetch()} title="Couldn't load your overview" />;

  const tracked = Object.values(data.status_counts).reduce((sum, n) => sum + n, 0);
  if (tracked === 0 && data.summary.approvals_pending === 0 && data.recent_detections.length === 0) return <FirstRun name={data.display_name} />;

  const t = data.today;
  const nothingToday = t.overdue.length + t.due_today.length === 0;
  const upcoming = data.upcoming.filter((g) => g.items.length > 0);
  const focusId = data.focus?.id;
  const inboxWaiting = (data.status_counts.NEEDS_REVIEW ?? 0) + data.summary.approvals_pending;

  return (
    <>
      <Greeting data={data} />
      <Focus ob={data.focus} />

      <div className="mt-8 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Today" description="What is late, and what is due before the day ends" />
            {nothingToday ? (
              <EmptyState icon={CircleCheck} title="A clear day" description="Nothing is overdue or due today." />
            ) : (
              <div className="px-1 py-3">
                {t.overdue.filter((o) => o.id !== focusId).length > 0 && (
                  <section className="mb-2">
                    <div className="px-4">
                      <SectionLabel tone="danger" count={t.overdue.length}>
                        Overdue
                      </SectionLabel>
                    </div>
                    <CommitmentList items={t.overdue} skipId={focusId} />
                  </section>
                )}
                {t.due_today.filter((o) => o.id !== focusId).length > 0 && (
                  <section className="mb-2">
                    <div className="px-4">
                      <SectionLabel count={t.due_today.length}>Due today</SectionLabel>
                    </div>
                    <CommitmentList items={t.due_today} skipId={focusId} />
                  </section>
                )}
              </div>
            )}
          </Card>

          <Card>
            <CardHeader title="Coming up" description="The next week" action={<Link href="/upcoming" className="text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">See all</Link>} />
            {upcoming.length === 0 ? (
              <EmptyState icon={Sunrise} title="Nothing coming up" description="Commitments with deadlines in the next week will be listed here." />
            ) : (
              <div className="px-1 py-3">
                {upcoming.map((g) => (
                  <section key={g.date} className="mb-2 last:mb-0">
                    <div className="px-4">
                      <SectionLabel>{g.label}</SectionLabel>
                    </div>
                    <CommitmentList items={g.items} />
                  </section>
                ))}
              </div>
            )}
          </Card>
        </div>

        <aside aria-label="At a glance" className="space-y-6">
          <Card>
            <CardHeader title="Waiting for you" action={<Inbox aria-hidden className="size-[18px] text-text-3" strokeWidth={1.6} />} />
            <div className="space-y-1 px-4 py-3">
              <StatLink href="/inbox" value={data.status_counts.NEEDS_REVIEW ?? 0} label="Commitments to review" tone="warn" />
              <StatLink href="/inbox" value={data.summary.approvals_pending} label="Actions to approve" tone="warn" />
            </div>
            {inboxWaiting === 0 && <p className="border-t border-line px-5 py-3 text-[13px] text-text-2">Nothing is waiting for a decision.</p>}
          </Card>

          <RecentDetections items={data.recent_detections} />

          <Card>
            <CardHeader title="Where things stand" action={<Activity aria-hidden className="size-[18px] text-text-3" strokeWidth={1.6} />} />
            <div className="px-5 py-4">
              <StatusBar counts={data.status_counts} />
            </div>
          </Card>

          <AutomationGlance data={data} />
        </aside>
      </div>
    </>
  );
}
