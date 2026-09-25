"use client";

import Link from "next/link";
import { STATUS } from "@/lib/labels";
import type { Status } from "@/lib/api";
import { cn } from "@/lib/cn";

const ORDER: Status[] = ["ESCALATED", "OVERDUE", "ACTION_REQUIRED", "NEEDS_REVIEW", "OPEN", "SCHEDULED", "COMPLETED"];
const FILL: Record<string, string> = {
  danger: "bg-danger",
  warn: "bg-warn",
  neutral: "bg-text-3",
  info: "bg-info",
  ok: "bg-ok",
};

/**
 * Where everything stands, as one restrained bar with its numbers written out beside it (the bar is a summary, never the only
 * carrier of information). Same palette as the rest of the product; no chart library for a single ratio.
 */
export function StatusBar({ counts }: { counts: Record<string, number> }) {
  const rows = ORDER.map((s) => ({ status: s, n: counts[s] ?? 0 })).filter((r) => r.n > 0);
  const total = rows.reduce((sum, r) => sum + r.n, 0);
  if (total === 0) return <p className="text-[14px] text-text-2">Nothing tracked yet.</p>;
  return (
    <div>
      <div role="img" aria-label={rows.map((r) => `${r.n} ${STATUS[r.status].label.toLowerCase()}`).join(", ")} className="flex h-2 overflow-hidden rounded-full bg-line">
        {rows.map((r) => (
          <span key={r.status} style={{ width: `${(r.n / total) * 100}%` }} className={cn("h-full border-r border-surface last:border-r-0", FILL[STATUS[r.status].tone])} />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {rows.map((r) => (
          <li key={r.status} className="flex items-center justify-between text-[13.5px]">
            <span className="flex items-center gap-2 text-text-2">
              <span aria-hidden className={cn("size-2 rounded-full", FILL[STATUS[r.status].tone])} />
              {STATUS[r.status].label}
            </span>
            <span data-numeric className="font-medium text-ink">
              {r.n}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function StatLink({ href, value, label, tone = "neutral" }: { href: string; value: number; label: string; tone?: "neutral" | "danger" | "warn" }) {
  return (
    <Link href={href} className="group flex items-baseline justify-between gap-3 rounded-md px-1 py-1.5 hover:bg-surface-2">
      <span className="text-[14px] text-text-2 group-hover:text-ink">{label}</span>
      <span data-numeric className={cn("text-[20px] font-semibold leading-6", tone === "danger" && value > 0 ? "text-danger" : tone === "warn" && value > 0 ? "text-warn" : "text-ink")}>
        {value}
      </span>
    </Link>
  );
}
