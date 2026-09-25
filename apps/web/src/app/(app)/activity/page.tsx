"use client";

import { History } from "lucide-react";
import Link from "next/link";
import { Suspense, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { ActivityItem } from "@/components/activity-item";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader, SectionLabel } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/field";
import { SkeletonRows } from "@/components/ui/skeleton";
import type { AuditEvent } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { useAudit } from "@/lib/queries";
import { dayHeading, localDayNumber } from "@/lib/time";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All activity" },
  { value: "COMMITMENT_DETECTED", label: "Commitments detected" },
  { value: "COMPLETED", label: "Completed" },
  { value: "NOTIFICATION_SENT", label: "Reminders sent" },
  { value: "OVERDUE_MARKED", label: "Marked overdue" },
  { value: "APPROVAL_REQUESTED", label: "Approvals requested" },
  { value: "ACTION_EXECUTED", label: "Actions carried out" },
  { value: "ACTION_FAILED", label: "Failed actions" },
  { value: "EXTRACTION_FAILED", label: "Failed detections" },
];

function group(events: AuditEvent[], timeZone: string, now: Date) {
  const days = new Map<number, { heading: string; events: AuditEvent[] }>();
  for (const event of events) {
    const at = new Date(event.created_at);
    const key = localDayNumber(at, timeZone);
    const day = days.get(key) ?? { heading: dayHeading(at, timeZone, now), events: [] };
    day.events.push(event);
    days.set(key, day);
  }
  return [...days.entries()].sort(([a], [b]) => b - a).map(([key, day]) => ({ key, ...day }));
}

function ActivityFeed() {
  const { now, timeZone } = useClock();
  const router = useRouter();
  const params = useSearchParams();
  const eventType = params.get("type") ?? "";
  const obligationId = params.get("obligation") ?? "";
  const { data, error, isPending, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useAudit({ event_type: eventType || undefined, obligation_id: obligationId || undefined });
  const events = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data]);
  const days = useMemo(() => group(events, timeZone, now), [events, timeZone, now]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/activity${next.size ? `?${next}` : ""}`);
  };

  return (
    <>
      <PageHeader
        title="Activity"
        description="Every detection, decision, reminder and action, in the order it happened. The record cannot be edited."
        actions={
          <div className="w-full sm:w-60">
            <Select aria-label="Filter activity" value={eventType} onChange={(e) => setParam("type", e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      {obligationId && (
        <p className="-mt-3 mb-5 text-[14px] text-text-2">
          Showing one commitment.{" "}
          <button type="button" onClick={() => setParam("obligation", "")} className="font-medium text-ink underline decoration-line-strong underline-offset-4">
            Show everything
          </button>
          {" · "}
          <Link href={`/obligations/${obligationId}`} className="font-medium text-ink underline decoration-line-strong underline-offset-4">
            Back to the commitment
          </Link>
        </p>
      )}
      {isPending ? (
        <Card>
          <SkeletonRows rows={6} message="Reading the record" />
        </Card>
      ) : error || !data ? (
        <ErrorPanel error={error} onRetry={() => void refetch()} title="Couldn't load the activity" />
      ) : days.length === 0 ? (
        <Card>
          <EmptyState icon={History} title="No activity to show" description={eventType || obligationId ? "Nothing matches this filter yet." : "Once something is detected, decided or sent, it is recorded here."} />
        </Card>
      ) : (
        <div className="space-y-7">
          {days.map((day) => (
            <section key={day.key} aria-label={day.heading}>
              <div className="px-1">
                <SectionLabel>{day.heading}</SectionLabel>
              </div>
              <Card className="overflow-hidden">
                <ul className="divide-y divide-line">
                  {day.events.map((event) => (
                    <ActivityItem key={event.id} event={event} timeZone={timeZone} showLink={!obligationId} />
                  ))}
                </ul>
              </Card>
            </section>
          ))}
          {hasNextPage && (
            <div className="flex justify-center">
              <Button loading={isFetchingNextPage} onClick={() => void fetchNextPage()}>
                Show older activity
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default function ActivityPage() {
  // useSearchParams needs a Suspense boundary so the rest of the page can render while the query string is read
  return (
    <Suspense fallback={<SkeletonRows rows={6} message="Reading the record" />}>
      <ActivityFeed />
    </Suspense>
  );
}
