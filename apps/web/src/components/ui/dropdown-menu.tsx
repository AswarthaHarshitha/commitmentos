"use client";

import * as Menu from "@radix-ui/react-dropdown-menu";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface MenuAction {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  tone?: "default" | "danger";
  disabled?: boolean;
}

export function DropdownMenu({ trigger, actions, align = "end", label }: { trigger: ReactNode; actions: (MenuAction | "separator")[]; align?: "start" | "end"; label?: string }) {
  return (
    <Menu.Root>
      <Menu.Trigger asChild aria-label={label}>
        {trigger}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align={align}
          sideOffset={6}
          className="z-50 min-w-48 rounded-lg border border-line-strong bg-surface p-1 shadow-pop data-[state=open]:animate-in"
        >
          {actions.map((action, i) =>
            action === "separator" ? (
              <Menu.Separator key={`sep-${i}`} className="my-1 h-px bg-line" />
            ) : (
              <Menu.Item
                key={action.label}
                disabled={action.disabled}
                onSelect={action.onSelect}
                className={cn(
                  "flex cursor-pointer select-none items-center gap-2.5 rounded-md px-2.5 py-2 text-[14px] outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-45",
                  action.tone === "danger" ? "text-danger data-[highlighted]:bg-danger-bg" : "text-text data-[highlighted]:bg-surface-2 data-[highlighted]:text-ink",
                )}
              >
                {action.icon && <action.icon aria-hidden className="size-4 text-current opacity-75" strokeWidth={1.7} />}
                {action.label}
              </Menu.Item>
            ),
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
