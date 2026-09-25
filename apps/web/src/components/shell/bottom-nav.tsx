"use client";

import { Activity, CircleCheck, Ellipsis, Settings, TriangleAlert, Workflow } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cn";
import { useNavCounts } from "@/lib/counts";
import { PRIMARY_NAV } from "@/lib/nav";
import { useAuth } from "@/lib/queries";
import { isActive } from "./sidebar";

const MAIN = PRIMARY_NAV.filter((i) => ["/overview", "/inbox", "/today", "/upcoming"].includes(i.href));

export function BottomNav() {
  const pathname = usePathname();
  const router = useRouter();
  const counts = useNavCounts();
  const { logout } = useAuth();
  const moreActive = ["/overdue", "/completed", "/automations", "/activity", "/settings"].some((p) => isActive(pathname, p));
  const item = "relative flex flex-1 flex-col items-center justify-center gap-1 py-2 text-[11.5px] font-medium";
  return (
    <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-40 border-t border-line-strong bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
      <ul className="flex">
        {MAIN.map((i) => {
          const active = isActive(pathname, i.href);
          const badge = i.badge ? counts[i.badge] : 0;
          return (
            <li key={i.href} className="flex flex-1">
              <Link href={i.href} aria-current={active ? "page" : undefined} className={cn(item, active ? "text-ink" : "text-text-2")}>
                <span className="relative">
                  <i.icon aria-hidden className="size-[22px]" strokeWidth={active ? 2 : 1.6} />
                  {badge > 0 && (
                    <span data-numeric aria-label={`${badge} waiting`} className="absolute -right-2.5 -top-1.5 min-w-[17px] rounded-full bg-ink px-1 text-center text-[10.5px] leading-[17px] text-surface">
                      {badge > 99 ? "99+" : badge}
                    </span>
                  )}
                </span>
                {i.label}
              </Link>
            </li>
          );
        })}
        <li className="flex flex-1">
          <DropdownMenu
            label="More"
            trigger={
              <button type="button" className={cn(item, "w-full", moreActive ? "text-ink" : "text-text-2")}>
                <Ellipsis aria-hidden className="size-[22px]" strokeWidth={1.6} />
                More
              </button>
            }
            actions={[
              { label: counts.overdue ? `Overdue (${counts.overdue})` : "Overdue", icon: TriangleAlert, onSelect: () => router.push("/overdue") },
              { label: "Completed", icon: CircleCheck, onSelect: () => router.push("/completed") },
              "separator",
              { label: "Automations", icon: Workflow, onSelect: () => router.push("/automations") },
              { label: "Activity", icon: Activity, onSelect: () => router.push("/activity") },
              { label: "Settings", icon: Settings, onSelect: () => router.push("/settings") },
              "separator",
              { label: "Sign out", onSelect: () => logout.mutate(undefined, { onSettled: () => router.replace("/login") }) },
            ]}
          />
        </li>
      </ul>
    </nav>
  );
}
