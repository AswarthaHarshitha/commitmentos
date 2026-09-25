"use client";

import { Archive, CircleCheck } from "lucide-react";
import { useState } from "react";
import { CommitmentList } from "@/components/commitment-list";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader } from "@/components/page-header";
import { SearchBox } from "@/components/search-box";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SkeletonRows } from "@/components/ui/skeleton";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/tabs";
import { useObligations } from "@/lib/queries";
import { useDebounced } from "@/lib/use-debounced";

type Closed = "COMPLETED" | "DISMISSED";

function ClosedList({ status, q }: { status: Closed; q: string }) {
  const { data, error, isPending, refetch } = useObligations({ view: "closed", status: [status], sort: "created", q: q || undefined });
  if (isPending) return <SkeletonRows rows={4} message="Loading your history" />;
  if (error || !data) return <ErrorPanel error={error} onRetry={() => refetch()} title="Couldn't load this list" />;
  if (data.items.length === 0) {
    return status === "COMPLETED" ? (
      <EmptyState icon={CircleCheck} title={q ? "Nothing matches that search" : "Nothing completed yet"} description={q ? "Try a different word." : "Commitments you finish are kept here, with their full history."} />
    ) : (
      <EmptyState icon={Archive} title={q ? "Nothing matches that search" : "Nothing dismissed"} description={q ? "Try a different word." : "Detections you dismissed are kept here in case you change your mind."} />
    );
  }
  return <CommitmentList items={data.items} readOnly />;
}

export default function CompletedPage() {
  const [tab, setTab] = useState<Closed>("COMPLETED");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  return (
    <>
      <PageHeader title="Completed" description="Everything you have finished or set aside. Open one to see exactly what happened." actions={<SearchBox value={search} onChange={setSearch} />} />
      <Tabs value={tab} onValueChange={(v) => setTab(v as Closed)}>
        <TabList label="Closed commitments">
          <Tab value="COMPLETED">Completed</Tab>
          <Tab value="DISMISSED">Dismissed</Tab>
        </TabList>
        <TabPanel value="COMPLETED">
          <Card className="overflow-hidden">
            <ClosedList status="COMPLETED" q={q} />
          </Card>
        </TabPanel>
        <TabPanel value="DISMISSED">
          <Card className="overflow-hidden">
            <ClosedList status="DISMISSED" q={q} />
          </Card>
        </TabPanel>
      </Tabs>
    </>
  );
}
