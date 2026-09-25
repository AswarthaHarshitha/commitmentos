import Link from "next/link";
import { cn } from "@/lib/cn";
import { actorLabel, eventStyle } from "@/lib/events";
import { formatTime } from "@/lib/time";
import type { AuditEvent } from "@/lib/api";
import type { Tone } from "@/lib/labels";

const ring: Record<Tone, string> = {
  neutral: "border-line-strong bg-surface text-text-2",
  ok: "border-ok-line bg-ok-bg text-ok",
  warn: "border-warn-line bg-warn-bg text-warn",
  danger: "border-danger-line bg-danger-bg text-danger",
  info: "border-info-line bg-info-bg text-info",
};

/** One line of the audit trail: what happened, who did it, and when. Read-only by design - the trail is append-only. */
export function ActivityItem({ event, timeZone, showLink = true }: { event: AuditEvent; timeZone: string; showLink?: boolean }) {
  const style = eventStyle(event.event_type);
  const actor = event.actor_type === "USER" ? "You" : actorLabel(`${event.actor_type.toLowerCase()}${event.actor_id ? `:${event.actor_id}` : ""}`);
  return (
    <li className="flex gap-3 px-4 py-3 sm:px-5">
      <span aria-hidden className={cn("mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border", ring[style.tone])}>
        <style.icon className="size-3.5" strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[14px] leading-5 text-ink">{event.message}</p>
        <p data-numeric className="mt-0.5 text-[12.5px] text-text-3">
          {formatTime(new Date(event.created_at), timeZone)} · {actor}
        </p>
      </div>
      {showLink && event.obligation_id && (
        <Link href={`/obligations/${event.obligation_id}`} className="shrink-0 self-center text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
          Open
        </Link>
      )}
    </li>
  );
}
