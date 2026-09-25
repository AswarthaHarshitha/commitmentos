"use client";

import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Tabs = RadixTabs.Root;

export function TabList({ children, label }: { children: ReactNode; label: string }) {
  return (
    <RadixTabs.List aria-label={label} className="flex gap-1 border-b border-line">
      {children}
    </RadixTabs.List>
  );
}

export function Tab({ value, children, count }: { value: string; children: ReactNode; count?: number }) {
  return (
    <RadixTabs.Trigger
      value={value}
      className={cn(
        "-mb-px inline-flex items-center gap-2 border-b-2 border-transparent px-3 py-2.5 text-[14px] font-medium text-text-2 transition-colors hover:text-ink",
        "data-[state=active]:border-ink data-[state=active]:text-ink",
      )}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span data-numeric className="rounded-full bg-surface-2 px-1.5 text-[12px] font-medium text-text-2 ring-1 ring-line">
          {count}
        </span>
      )}
    </RadixTabs.Trigger>
  );
}

export const TabPanel = ({ value, children }: { value: string; children: ReactNode }) => (
  <RadixTabs.Content value={value} className="pt-4 outline-none">
    {children}
  </RadixTabs.Content>
);
