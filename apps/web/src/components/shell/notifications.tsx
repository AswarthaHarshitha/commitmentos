"use client";

import * as Popover from "@radix-ui/react-popover";
import { Bell } from "lucide-react";
import Link from "next/link";
import { useClock } from "@/lib/clock";
import { cn } from "@/lib/cn";
import { useMarkNotificationsRead, useNotifications } from "@/lib/queries";
import { timeAgo } from "@/lib/time";

export function NotificationsBell() {
  const { data } = useNotifications();
  const mark = useMarkNotificationsRead();
  const { now, timeZone } = useClock();
  const unread = data?.unread ?? 0;
  const items = data?.items.filter((n) => n.channel === "IN_APP").slice(0, 8) ?? [];
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
          className="relative grid size-9 place-items-center rounded-md text-text-2 hover:bg-surface hover:text-ink"
        >
          <Bell aria-hidden className="size-[19px]" strokeWidth={1.7} />
          {unread > 0 && <span aria-hidden className="absolute right-2 top-2 size-2 rounded-full bg-danger ring-2 ring-bg" />}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="z-50 w-[min(92vw,380px)] rounded-lg border border-line-strong bg-surface shadow-pop">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 className="text-[14px] font-semibold text-ink">Notifications</h2>
            {unread > 0 && (
              <button type="button" onClick={() => mark.mutate(undefined)} className="text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
                Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="px-4 py-8 text-center text-[14px] text-text-2">You&apos;re all caught up.</p>
          ) : (
            <ul className="max-h-[60dvh] divide-y divide-line overflow-y-auto">
              {items.map((n) => (
                <li key={n.id}>
                  <Link
                    href={n.obligation_id ? `/obligations/${n.obligation_id}` : "/activity"}
                    onClick={() => !n.read_at && mark.mutate(n.id)}
                    className={cn("block px-4 py-3 hover:bg-surface-2", !n.read_at && "bg-warn-bg/40")}
                  >
                    <p className="flex items-start gap-2 text-[14px] font-medium text-ink">
                      {!n.read_at && <span aria-label="Unread" className="mt-2 size-1.5 shrink-0 rounded-full bg-ink" />}
                      <span>{n.title}</span>
                    </p>
                    {n.body && <p className="mt-0.5 line-clamp-2 text-[13px] text-text-2">{n.body.split("\n")[0]}</p>}
                    <p className="mt-1 text-[12.5px] text-text-3">{timeAgo(n.created_at, now, timeZone)}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
