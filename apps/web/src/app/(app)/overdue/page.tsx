"use client";

import { CircleCheck } from "lucide-react";
import { CommitmentSection } from "@/components/commitment-section";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SkeletonRows } from "@/components/ui/skeleton";
import { useObligations } from "@/lib/queries";

export default function OverduePage() {
  const { data, error, isPending, refetch } = useObligations({ view: "overdue", sort: "due" });
  const mine = data?.items.filter((o) => o.owner === "me") ?? [];
  const theirs = data?.items.filter((o) => o.owner !== "me") ?? [];

  return (
    <>
      <PageHeader
        title="Overdue"
        description={data && data.total > 0 ? "These have passed their deadline. Complete them, move the deadline, or follow up." : "What has slipped past its deadline."}
      />
      {isPending ? (
        <Card>
          <SkeletonRows rows={3} message="Checking what is late" />
        </Card>
      ) : error || !data ? (
        <ErrorPanel error={error} onRetry={() => refetch()} title="Couldn't load overdue commitments" />
      ) : data.items.length === 0 ? (
        <Card>
          <EmptyState icon={CircleCheck} title="Nothing is overdue" description="Every deadline so far has been met or is still ahead. That is the point of all this." />
        </Card>
      ) : (
        <div className="space-y-7">
          <CommitmentSection title="You owe" tone="danger" items={mine} note="Yours to finish, or to reschedule." />
          <CommitmentSection title="Waiting on others" tone="danger" items={theirs} note="Someone else owes you these. A follow-up can be drafted from the commitment." />
        </div>
      )}
    </>
  );
}
