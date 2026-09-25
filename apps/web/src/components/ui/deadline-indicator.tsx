import { Clock } from "lucide-react";
import { cn } from "@/lib/cn";
import type { DueInfo, DueTone } from "@/lib/time";

const toneClass: Record<DueTone, string> = {
  calm: "text-text-2",
  soon: "text-warn",
  overdue: "text-danger",
  done: "text-ok",
  none: "text-text-3",
};

/**
 * "Due tomorrow · 5:00 PM", "Overdue by 2 days". Colour is reserved for what needs attention: amber inside 24 hours,
 * red only once the deadline has passed. The words carry the meaning too, so colour is never the only signal.
 */
export function DeadlineIndicator({ info, size = "sm", className }: { info: DueInfo; size?: "sm" | "lg"; className?: string }) {
  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-x-2", toneClass[info.tone], className)}>
      <span className={cn("inline-flex items-center gap-1.5 font-medium", size === "lg" ? "text-[17px]" : "text-[13.5px]")}>
        <Clock aria-hidden className={cn("shrink-0", size === "lg" ? "size-[18px]" : "size-3.5")} strokeWidth={1.8} />
        {info.label}
      </span>
      {info.detail && (
        <span data-numeric className={cn("text-text-2", size === "lg" ? "text-[15px]" : "text-[13px]")}>
          · {info.detail}
        </span>
      )}
    </span>
  );
}
