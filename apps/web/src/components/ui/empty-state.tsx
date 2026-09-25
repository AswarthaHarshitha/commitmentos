import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** Designed empty states: say what this place is for and what to do next - never a bare "No data". */
export function EmptyState({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <span aria-hidden className="mb-4 grid size-11 place-items-center rounded-full border border-line bg-surface-2 text-text-2">
        <Icon className="size-5" strokeWidth={1.6} />
      </span>
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-[14px] text-text-2 text-balance-soft">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
