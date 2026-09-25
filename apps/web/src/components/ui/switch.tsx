"use client";

import * as RadixSwitch from "@radix-ui/react-switch";
import { useId, type ReactNode } from "react";

export function Toggle({ checked, onCheckedChange, label, hint, disabled }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string; hint?: ReactNode; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-6">
      <div>
        <label htmlFor={id} className="text-[14px] font-medium text-ink">
          {label}
        </label>
        {hint && <p className="mt-0.5 text-[13px] text-text-2">{hint}</p>}
      </div>
      <RadixSwitch.Root
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        className="relative mt-0.5 h-5 w-9 shrink-0 rounded-full bg-line-strong transition-colors data-[state=checked]:bg-ink disabled:opacity-50"
      >
        <RadixSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-surface shadow-card transition-transform data-[state=checked]:translate-x-[18px]" />
      </RadixSwitch.Root>
    </div>
  );
}
