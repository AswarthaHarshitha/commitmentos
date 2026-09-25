"use client";

import { CalendarRange } from "lucide-react";
import { useMemo, useState } from "react";
import { CommitmentSection } from "@/components/commitment-section";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader } from "@/components/page-header";
import { SearchBox } from "@/components/search-box";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { SkeletonRows } from "@/components/ui/skeleton";
import type { Obligation } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { useObligations } from "@/lib/queries";
import { dayHeading, localDayNumber } from "@/lib/time";
import { useDebounced } from "@/lib/use-debounced";

function groupByDay(items: Obligation[], timeZone: string, now: Date) {
  const groups = new Map<number, { heading: string; items: Obligation[] }>();
  for (const ob of items) {
    if (!ob.due_at) continue;
    const due = new Date(ob.due_at);
    const key = localDayNumber(due, timeZone);
    const group = groups.get(key) ?? { heading: dayHeading(due, timeZone, now), items: [] };
    group.items.push(ob);
    groups.set(key, group);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b).map(([key, group]) => ({ key, ...group }));
}

export default function UpcomingPage() {
  const { now, timeZone } = useClock();
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const { data, error, isPending, refetch } = useObligations({ view: "upcoming", sort: "due", q: q || undefined });
  const groups = useMemo(() => groupByDay(data?.items ?? [], timeZone, now), [data, timeZone, now]);

  return (
    <>
      <PageHeader title="Upcoming" description="Everything with a deadline ahead, soonest first." actions={<SearchBox value={search} onChange={setSearch} />} />
      {isPending ? (
        <Card>
          <SkeletonRows rows={4} message="Looking ahead" />
        </Card>
      ) : error || !data ? (
        <ErrorPanel error={error} onRetry={() => refetch()} title="Couldn't load upcoming commitments" />
      ) : groups.length === 0 ? (
        <Card>
          <EmptyState
            icon={CalendarRange}
            title={q ? "Nothing matches that search" : "Nothing coming up"}
            description={q ? "Try a different word, or clear the search." : "Commitments with a deadline still ahead will be listed here, day by day."}
          />
        </Card>
      ) : (
        <div className="space-y-7">
          {groups.map((g) => (
            <CommitmentSection key={g.key} title={g.heading} items={g.items} />
          ))}
        </div>
      )}
    </>
  );
}
