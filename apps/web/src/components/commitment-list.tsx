"use client";

import { LayoutGroup } from "framer-motion";
import type { Obligation } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { CommitmentCard } from "./commitment-card";

/** A run of commitment rows separated by hairlines, sharing layout animation so a completed row settles smoothly. */
export function CommitmentList({ items, readOnly, skipId }: { items: Obligation[]; readOnly?: boolean; skipId?: string }) {
  const { now, timeZone } = useClock();
  const visible = skipId ? items.filter((o) => o.id !== skipId) : items;
  return (
    <LayoutGroup>
      <div className="divide-y divide-line">
        {visible.map((ob) => (
          <CommitmentCard key={ob.id} ob={ob} now={now} timeZone={timeZone} readOnly={readOnly} />
        ))}
      </div>
    </LayoutGroup>
  );
}
