"use client";

import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = ({ children }: { children: ReactNode }) => <RadixTooltip.Provider delayDuration={350}>{children}</RadixTooltip.Provider>;

/** Extra explanation on hover or keyboard focus. Never the only place a fact appears. */
export function Tip({ label, children, side = "top" }: { label: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content side={side} sideOffset={6} className="z-[70] max-w-64 rounded-md bg-ink px-2.5 py-1.5 text-[12.5px] leading-snug text-surface shadow-pop">
          {label}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
