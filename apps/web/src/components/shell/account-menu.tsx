"use client";

import { LogOut, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useAuth, useMe } from "@/lib/queries";
import { cn } from "@/lib/cn";

function initials(name: string | undefined, email: string | undefined): string {
  const source = (name || email || "?").trim();
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? (parts[1]?.[0] ?? "") : "")).toUpperCase();
}

export function AccountMenu({ compact = false, className }: { compact?: boolean; className?: string }) {
  const { data: me } = useMe();
  const { logout } = useAuth();
  const router = useRouter();
  const label = me?.display_name || me?.email || "Account";
  return (
    <DropdownMenu
      align={compact ? "end" : "start"}
      label="Account menu"
      trigger={
        <button
          type="button"
          className={cn("flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface", className)}
        >
          <span aria-hidden className="grid size-8 shrink-0 place-items-center rounded-full border border-line-strong bg-surface text-[12px] font-semibold text-ink">
            {initials(me?.display_name, me?.email)}
          </span>
          {!compact && (
            <span className="hidden min-w-0 lg:block">
              <span className="block truncate text-[13.5px] font-medium text-ink">{label}</span>
              <span className="block truncate text-[12.5px] text-text-3">{me?.timezone}</span>
            </span>
          )}
        </button>
      }
      actions={[
        { label: "Settings", icon: Settings, onSelect: () => router.push("/settings") },
        "separator",
        { label: "Sign out", icon: LogOut, onSelect: () => logout.mutate(undefined, { onSettled: () => router.replace("/login") }) },
      ]}
    />
  );
}
