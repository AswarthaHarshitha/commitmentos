"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/cn";

/**
 * The checkbox that completes a commitment. On completion the ring fills and the tick is drawn (about a quarter of a second).
 * With reduced motion the state simply changes: <MotionConfig reducedMotion="user"> makes the transitions instant.
 */
export function CompletionCheck({ checked, onToggle, label, disabled, busy }: { checked: boolean; onToggle: () => void; label: string; disabled?: boolean; busy?: boolean }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      aria-busy={busy || undefined}
      disabled={disabled || busy}
      onClick={onToggle}
      className={cn(
        "group relative grid size-[22px] shrink-0 place-items-center rounded-full border transition-colors duration-200",
        checked ? "border-ok bg-ok" : "border-line-strong bg-surface hover:border-ink",
        "disabled:cursor-not-allowed",
      )}
    >
      <svg viewBox="0 0 20 20" aria-hidden className="size-[18px]">
        <motion.path
          d="M5.2 10.4l3.1 3.1 6.5-6.7"
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className={checked ? "stroke-surface" : "stroke-line-strong group-hover:stroke-ink"}
          initial={false}
          animate={{ pathLength: checked ? 1 : 0.0001, opacity: checked ? 1 : 0 }}
          whileHover={checked ? undefined : { opacity: 0.55, pathLength: 1 }}
          transition={{ duration: 0.26, ease: [0.2, 0.7, 0.2, 1] }}
        />
      </svg>
    </button>
  );
}
