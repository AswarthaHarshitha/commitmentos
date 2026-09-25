"use client";

import { Check, X } from "lucide-react";
import Link from "next/link";
import { TypeIcon } from "@/components/commitment-card";
import { Button } from "@/components/ui/button";
import { DeadlineIndicator } from "@/components/ui/deadline-indicator";
import { useDecisions } from "@/lib/actions";
import type { Obligation } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { contextLine } from "@/lib/context";
import { SOURCE_LABEL } from "@/lib/labels";
import { describeDeadline, timeAgo } from "@/lib/time";

/** Created automatically with enough confidence to track it straight away; shown here once so nothing is added behind your back. */
export function NewItem({ ob }: { ob: Obligation }) {
  const { now, timeZone } = useClock();
  const { accept, dismiss, busyId } = useDecisions();
  const busy = busyId === ob.id;
  const due = describeDeadline({ dueAt: ob.due_at, precision: ob.due_precision, timeZone, now });
  return (
    <li className="px-4 py-4 sm:px-5" data-testid="new-item">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="text-[12.5px] text-text-2">
            Added automatically · {SOURCE_LABEL[ob.source] ?? "a message"} · {timeAgo(ob.created_at, now, timeZone)}
          </p>
          <h3 className="mt-1 text-[15px] font-medium text-ink">
            <Link href={`/obligations/${ob.id}`} className="decoration-line-strong underline-offset-4 hover:underline">
              {ob.title}
            </Link>
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[13.5px] text-text-2">
            <TypeIcon type={ob.obligation_type} />
            <span>{contextLine(ob)}</span>
          </p>
          <div className="mt-2">
            <DeadlineIndicator info={due} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" icon={<Check aria-hidden className="size-4" strokeWidth={2} />} loading={busy} onClick={() => accept(ob)}>
            Looks right
          </Button>
          <Button size="sm" variant="ghost" icon={<X aria-hidden className="size-4" strokeWidth={1.8} />} disabled={busy} onClick={() => dismiss(ob)}>
            Dismiss
          </Button>
        </div>
      </div>
    </li>
  );
}
