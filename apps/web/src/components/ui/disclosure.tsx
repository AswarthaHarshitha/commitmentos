"use client";

import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A quiet show/hide. `onToggle` lets the caller fetch the detail only once it is asked for. */
export function Disclosure({ label, children, onToggle, className }: { label: string; children: ReactNode; onToggle?: (open: boolean) => void; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setOpen(!open);
          onToggle?.(!open);
        }}
        className="inline-flex items-center gap-1 rounded-md py-1 text-[13px] font-medium text-text-2 hover:text-ink"
      >
        <ChevronDown aria-hidden className={cn("size-4 transition-transform duration-200", open && "rotate-180")} strokeWidth={1.8} />
        {label}
      </button>
      <div id={id} hidden={!open} className="mt-1">
        {open && children}
      </div>
    </div>
  );
}
