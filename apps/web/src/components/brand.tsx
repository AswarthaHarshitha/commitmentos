import { cn } from "@/lib/cn";

/** The mark: a ledger line with a tick - a promise, kept. Drawn in ink; no gradient, no glow. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 28 28" aria-hidden className={cn("size-7", className)}>
      <rect x="1" y="1" width="26" height="26" rx="7" className="fill-ink" />
      <path d="M8 15.2l3.6 3.6L20 9.6" fill="none" className="stroke-surface" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 21.4h12" className="stroke-surface" strokeOpacity="0.45" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function Brand({ className, showWord = true }: { className?: string; showWord?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark />
      {showWord && <span className="text-[16px] font-semibold tracking-[-0.015em] text-ink">CommitmentOS</span>}
    </span>
  );
}
