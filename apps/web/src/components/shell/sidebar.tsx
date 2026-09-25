"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Brand, BrandMark } from "@/components/brand";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { useNavCounts } from "@/lib/counts";
import { PRIMARY_NAV, SYSTEM_NAV, type NavItem } from "@/lib/nav";
import { AccountMenu } from "./account-menu";

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function CountBadge({ kind, value }: { kind: "inbox" | "overdue"; value: number }) {
  if (value <= 0) return null;
  return (
    <span
      data-numeric
      aria-label={`${value} ${kind === "inbox" ? "waiting for you" : "overdue"}`}
      className={cn(
        "ml-auto rounded-full px-1.5 text-[12px] font-medium leading-5",
        kind === "overdue" ? "bg-danger-bg text-danger ring-1 ring-danger-line" : "bg-ink text-surface",
        "max-lg:absolute max-lg:right-1.5 max-lg:top-1 max-lg:min-w-[18px] max-lg:px-1 max-lg:text-center max-lg:text-[11px] max-lg:leading-[18px]",
      )}
    >
      {value > 99 ? "99+" : value}
    </span>
  );
}

function NavLink({ item, active, counts }: { item: NavItem; active: boolean; counts: { inbox: number; overdue: number } }) {
  const link = (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex items-center gap-3 rounded-md px-3 py-2 text-[14px] font-medium transition-colors max-lg:justify-center max-lg:px-0",
        active ? "bg-surface text-ink shadow-card ring-1 ring-line" : "text-text-2 hover:bg-surface hover:text-ink",
      )}
    >
      <item.icon aria-hidden className="size-[18px] shrink-0" strokeWidth={1.7} />
      <span className="max-lg:sr-only">{item.label}</span>
      {item.badge && <CountBadge kind={item.badge} value={counts[item.badge]} />}
    </Link>
  );
  return (
    <li>
      <span className="lg:hidden">
        <Tip label={item.label} side="right">
          {link}
        </Tip>
      </span>
      <span className="max-lg:hidden">{link}</span>
    </li>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const counts = useNavCounts();
  return (
    <aside aria-label="Primary" className="sticky top-0 hidden h-dvh w-[68px] shrink-0 flex-col border-r border-line bg-surface-2/70 md:flex lg:w-60">
      <div className="flex h-16 items-center px-4 max-lg:justify-center lg:px-5">
        <Link href="/overview" aria-label="CommitmentOS overview">
          <span className="lg:hidden">
            <BrandMark />
          </span>
          <span className="max-lg:hidden">
            <Brand />
          </span>
        </Link>
      </div>
      <nav aria-label="Main" className="flex-1 overflow-y-auto px-2 py-2 lg:px-3">
        <ul className="space-y-0.5">
          {PRIMARY_NAV.map((item) => (
            <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} counts={counts} />
          ))}
        </ul>
        <div aria-hidden className="mx-3 my-3 h-px bg-line" />
        <ul className="space-y-0.5">
          {SYSTEM_NAV.map((item) => (
            <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} counts={counts} />
          ))}
        </ul>
      </nav>
      <div className="border-t border-line p-2 lg:p-3">
        <AccountMenu />
      </div>
    </aside>
  );
}
