"use client";

import { CircleCheck } from "lucide-react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ErrorPanel } from "@/components/error-panel";
import { ApprovalItem, DecidedApproval } from "@/components/inbox/approval-item";
import { NewItem } from "@/components/inbox/new-item";
import { CandidateItem, ReviewItem } from "@/components/inbox/review-item";
import { PageHeader, SectionLabel } from "@/components/page-header";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/tabs";
import { useApprovals, useCandidates, useObligations } from "@/lib/queries";

function Nothing({ title, description }: { title: string; description: string }) {
  return <EmptyState icon={CircleCheck} title={title} description={description} />;
}

function ReviewTab() {
  const review = useObligations({ view: "review", sort: "created" });
  const candidates = useCandidates();
  if (review.isPending || candidates.isPending) return <SkeletonRows rows={3} message="Fetching what needs your eyes" />;
  if (review.error || candidates.error) return <ErrorPanel error={review.error ?? candidates.error} onRetry={() => void Promise.all([review.refetch(), candidates.refetch()])} />;
  const items = review.data?.items ?? [];
  const flagged = candidates.data ?? [];
  if (items.length + flagged.length === 0) return <Nothing title="Nothing to review" description="When a message might be a commitment but CommitmentOS is not sure, it waits here for you." />;
  return (
    <ul className="divide-y divide-line">
      {items.map((ob) => (
        <ReviewItem key={ob.id} ob={ob} />
      ))}
      {flagged.map((c) => (
        <CandidateItem key={c.id} candidate={c} />
      ))}
    </ul>
  );
}

function ApprovalsTab() {
  const { data, error, isPending, refetch } = useApprovals();
  if (isPending) return <SkeletonRows rows={2} message="Fetching proposed actions" />;
  if (error || !data) return <ErrorPanel error={error} onRetry={() => refetch()} />;
  const pending = data.filter((a) => a.status === "PENDING");
  const decided = data.filter((a) => a.status !== "PENDING").slice(0, 8);
  return (
    <div>
      {pending.length === 0 ? (
        <Nothing title="No actions to approve" description="Follow-up emails and calendar events are drafted for you, and wait here. Nothing is sent without your approval." />
      ) : (
        <ul className="divide-y divide-line">
          {pending.map((a) => (
            <ApprovalItem key={a.id} approval={a} />
          ))}
        </ul>
      )}
      {decided.length > 0 && (
        <div className="border-t border-line bg-surface-2/50 py-3">
          <div className="px-4 sm:px-5">
            <SectionLabel>Recently decided</SectionLabel>
          </div>
          <ul className="divide-y divide-line">
            {decided.map((a) => (
              <DecidedApproval key={a.id} approval={a} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function NewTab() {
  const { data, error, isPending, refetch } = useObligations({ view: "inbox", sort: "created" });
  if (isPending) return <SkeletonRows rows={3} message="Fetching new commitments" />;
  if (error || !data) return <ErrorPanel error={error} onRetry={() => refetch()} />;
  // anything still awaiting review is on the other tab; this one is what was created automatically and not yet seen
  const items = data.items.filter((o) => o.status !== "NEEDS_REVIEW" && o.status !== "DETECTED");
  if (items.length === 0) return <Nothing title="You have seen everything" description="Commitments CommitmentOS creates on its own appear here until you have looked at them." />;
  return (
    <ul className="divide-y divide-line">
      {items.map((ob) => (
        <NewItem key={ob.id} ob={ob} />
      ))}
    </ul>
  );
}

const TABS = ["review", "approvals", "new"];

function InboxContent() {
  const router = useRouter();
  const params = useSearchParams();
  const requested = params.get("tab") ?? "";
  const tab = TABS.includes(requested) ? requested : "review";
  const setTab = (value: string) => router.replace(value === "review" ? "/inbox" : `/inbox?tab=${value}`, { scroll: false });
  const review = useObligations({ view: "review", sort: "created" });
  const candidates = useCandidates();
  const approvals = useApprovals("PENDING");
  const fresh = useObligations({ view: "inbox", sort: "created" });
  const reviewCount = (review.data?.total ?? 0) + (candidates.data?.length ?? 0);
  const freshCount = (fresh.data?.items ?? []).filter((o) => o.status !== "NEEDS_REVIEW" && o.status !== "DETECTED").length;
  const waiting = reviewCount + (approvals.data?.length ?? 0);

  return (
    <>
      <PageHeader
        title="Inbox"
        description={waiting > 0 ? `${waiting} ${waiting === 1 ? "thing needs" : "things need"} a decision from you.` : "Everything CommitmentOS found or proposed, waiting for you to decide."}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabList label="Inbox sections">
          <Tab value="review" count={reviewCount}>
            To review
          </Tab>
          <Tab value="approvals" count={approvals.data?.length}>
            Approvals
          </Tab>
          <Tab value="new" count={freshCount}>
            New
          </Tab>
        </TabList>
        <TabPanel value="review">
          <Card className="overflow-hidden">
            <ReviewTab />
          </Card>
        </TabPanel>
        <TabPanel value="approvals">
          <Card className="overflow-hidden">
            <ApprovalsTab />
          </Card>
        </TabPanel>
        <TabPanel value="new">
          <Card className="overflow-hidden">
            <NewTab />
          </Card>
        </TabPanel>
      </Tabs>
    </>
  );
}

export default function InboxPage() {
  // useSearchParams needs a Suspense boundary so the rest of the page can render while the query string is read
  return (
    <Suspense fallback={<SkeletonRows rows={3} message="Opening your inbox" />}>
      <InboxContent />
    </Suspense>
  );
}
