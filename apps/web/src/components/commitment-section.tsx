import type { ReactNode } from "react";
import type { Obligation } from "@/lib/api";
import { CommitmentList } from "./commitment-list";
import { SectionLabel } from "./page-header";
import { Card } from "./ui/card";

/** A titled group of commitments in one card. Renders nothing when empty, so a page only shows the groups it has. */
export function CommitmentSection({ title, note, items, tone, readOnly, skipId }: { title: string; note?: ReactNode; items: Obligation[]; tone?: "neutral" | "danger"; readOnly?: boolean; skipId?: string }) {
  const visible = skipId ? items.filter((o) => o.id !== skipId) : items;
  if (visible.length === 0) return null;
  return (
    <section aria-label={title}>
      <div className="px-1">
        <SectionLabel tone={tone} count={visible.length}>
          {title}
        </SectionLabel>
        {note && <p className="-mt-1 mb-2 px-1 text-[13px] text-text-2">{note}</p>}
      </div>
      <Card className="overflow-hidden">
        <CommitmentList items={visible} readOnly={readOnly} />
      </Card>
    </section>
  );
}
