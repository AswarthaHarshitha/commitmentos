import { motion } from "framer-motion";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Tone } from "@/lib/labels";

const dotTone: Record<Tone, string> = {
  neutral: "border-line-strong bg-surface text-text-2",
  ok: "border-ok-line bg-ok-bg text-ok",
  warn: "border-warn-line bg-warn-bg text-warn",
  danger: "border-danger-line bg-danger-bg text-danger",
  info: "border-info-line bg-info-bg text-info",
};

export interface TimelineItemProps {
  icon: LucideIcon;
  tone?: Tone;
  title: ReactNode;
  meta?: ReactNode;
  /** an effect hangs off the previous step: smaller, indented, joined by an elbow */
  effect?: boolean;
  planned?: boolean;
  last?: boolean;
  /** an event that arrived while the page was open eases in; what was already there does not move */
  animateIn?: boolean;
}

/**
 * A vertical timeline that shows causality: a step (a deadline passing, an approval) and the things it caused (a notification queued,
 * then sent) are joined, so the history reads as "this, therefore that". Planned steps are dashed and quiet.
 */
export function Timeline({ children, label }: { children: ReactNode; label: string }) {
  return (
    <ol aria-label={label} className="relative">
      {children}
    </ol>
  );
}

export function TimelineItem({ icon: Icon, tone = "neutral", title, meta, effect = false, planned = false, last = false, animateIn = false }: TimelineItemProps) {
  return (
    <motion.li
      initial={animateIn ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.2, 0.7, 0.2, 1] }}
      className={cn("relative flex gap-3", effect ? "pl-6" : "", last ? "pb-0" : effect ? "pb-3" : "pb-5")}
    >
      {!last && <span aria-hidden className={cn("absolute w-px bg-line-strong", effect ? "left-[19px] top-6 bottom-0" : "left-[13px] top-8 bottom-0", planned && "bg-transparent [background-image:linear-gradient(var(--color-line-strong)_50%,transparent_50%)] [background-size:1px_6px]")} />}
      <span
        aria-hidden
        className={cn(
          "relative z-10 grid shrink-0 place-items-center rounded-full border",
          effect ? "size-5" : "size-7",
          planned ? "border-dashed border-line-strong bg-surface text-text-3" : dotTone[tone],
        )}
      >
        <Icon className={effect ? "size-3" : "size-3.5"} strokeWidth={1.8} />
      </span>
      <div className={cn("min-w-0 flex-1", effect ? "pt-px" : "pt-0.5")}>
        <p className={cn("text-[14px] leading-5", planned ? "text-text-2" : "text-ink", effect && "text-[13.5px] text-text-2")}>{title}</p>
        {meta && <p className="mt-0.5 text-[12.5px] text-text-3">{meta}</p>}
      </div>
    </motion.li>
  );
}
