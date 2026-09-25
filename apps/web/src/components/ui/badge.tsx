import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/labels";

const tones: Record<Tone, { box: string; dot: string }> = {
  neutral: { box: "bg-surface-2 text-text-2 border-line", dot: "bg-text-3" },
  ok: { box: "bg-ok-bg text-ok border-ok-line", dot: "bg-ok" },
  warn: { box: "bg-warn-bg text-warn border-warn-line", dot: "bg-warn" },
  danger: { box: "bg-danger-bg text-danger border-danger-line", dot: "bg-danger" },
  info: { box: "bg-info-bg text-info border-info-line", dot: "bg-info" },
};

export function Badge({ tone = "neutral", dot = false, children, className }: { tone?: Tone; dot?: boolean; children: ReactNode; className?: string }) {
  const t = tones[tone];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[12px] font-medium leading-4", t.box, className)}>
      {dot && <span aria-hidden className={cn("size-1.5 rounded-full", t.dot)} />}
      {children}
    </span>
  );
}

/** A quieter status marker for dense lists: a coloured dot and plain text, no box. */
export function StatusIndicator({ tone = "neutral", label, className }: { tone?: Tone; label: string; className?: string }) {
  const t = tones[tone];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[13px] text-text-2", className)}>
      <span aria-hidden className={cn("size-1.5 rounded-full", t.dot)} />
      {label}
    </span>
  );
}
