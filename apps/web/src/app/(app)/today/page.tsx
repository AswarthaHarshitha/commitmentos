"use client";

import { CircleCheck } from "lucide-react";
import { CommitmentSection } from "@/components/commitment-section";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { useClock } from "@/lib/clock";
import { useDashboard } from "@/lib/queries";
import { formatDay } from "@/lib/time";

export default function TodayPage() {
  const { now, timeZone } = useClock();
  const { data, error, isPending, refetch } = useDashboard();
  const weekday = new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone }).format(now);

  if (isPending) {
    return (
      <div aria-busy="true">
        <Skeleton className="mb-8 h-9 w-48" />
        <Card>
          <SkeletonRows rows={3} message="Gathering what is due today" />
        </Card>
      </div>
    );
  }
  if (error || !data) return <ErrorPanel error={error} onRetry={() => refetch()} title="Couldn't load today" />;

  const { overdue, due_today } = data.today;
  const empty = overdue.length + due_today.length === 0;
  return (
    <>
      <PageHeader eyebrow={`${weekday}, ${formatDay(now, timeZone, now)}`} title="Today" description={data.summary.headline} />
      {empty ? (
        <Card>
          <EmptyState icon={CircleCheck} title="Nothing is due today" description="No commitment is overdue or due before the day ends. What is coming next is under Upcoming." />
        </Card>
      ) : (
        <div className="space-y-7">
          <CommitmentSection title="Overdue" tone="danger" items={overdue} note="Already past their deadline." />
          <CommitmentSection title="Due today" items={due_today} />
        </div>
      )}
    </>
  );
}
