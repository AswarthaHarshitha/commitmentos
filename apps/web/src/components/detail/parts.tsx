import { CalendarCheck, ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import type { AppNotification, AutomationRun, CalendarEvent, Obligation } from "@/lib/api";
import type { Tone } from "@/lib/labels";
import { RUN_STATUS } from "@/lib/labels";
import { formatDuration, formatMoment, formatRange, timeAgo } from "@/lib/time";

export function ConfidenceMeter({ value }: { value: number }) {
  const percent = Math.round(value * 100);
  const words = value >= 0.85 ? "High" : value >= 0.6 ? "Medium" : "Low";
  return (
    <div className="flex items-center gap-3">
      <div role="img" aria-label={`Confidence ${percent} percent`} className="h-1.5 w-28 overflow-hidden rounded-full bg-line">
        <div className="h-full rounded-full bg-ink" style={{ width: `${percent}%` }} />
      </div>
      <span data-numeric className="text-[14px] text-ink">
        {percent}%
      </span>
      <span className="text-[13px] text-text-2">{words}</span>
    </div>
  );
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[9rem_1fr]">
      <dt className="text-[13px] text-text-2">{label}</dt>
      <dd className="min-w-0 text-[14px] text-ink">{children}</dd>
    </div>
  );
}

const NOTIFICATION_TONE: Record<AppNotification["status"], Tone> = { PENDING: "info", SENDING: "info", SENT: "ok", FAILED: "danger", CANCELLED: "neutral", SKIPPED: "neutral" };
const NOTIFICATION_WORD: Record<AppNotification["status"], string> = { PENDING: "Queued", SENDING: "Sending", SENT: "Sent", FAILED: "Failed", CANCELLED: "Cancelled", SKIPPED: "Skipped" };
const CHANNEL_WORD = { IN_APP: "In the app", EMAIL: "Email", TELEGRAM: "Telegram" } as const;

export function RemindersCard({ items, now, timeZone }: { items: AppNotification[]; now: Date; timeZone: string }) {
  return (
    <Card>
      <CardHeader title="Reminders" description="What has been queued or sent about this" />
      {items.length === 0 ? (
        <p className="px-5 py-4 text-[14px] text-text-2">No reminders yet. They are scheduled from the deadline.</p>
      ) : (
        <ul className="divide-y divide-line">
          {items.map((n) => (
            <li key={n.id} className="px-5 py-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-[14px] text-ink">{n.title}</p>
                <Badge tone={NOTIFICATION_TONE[n.status]} dot>
                  {NOTIFICATION_WORD[n.status]}
                </Badge>
              </div>
              <p className="mt-0.5 text-[12.5px] text-text-3">
                {CHANNEL_WORD[n.channel]} · {timeAgo(n.sent_at ?? n.scheduled_for, now, timeZone)}
              </p>
              {n.status === "FAILED" && n.last_error && <p className="mt-1 text-[12.5px] text-danger">{n.last_error}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function CalendarCard({ events, timeZone, now }: { events: CalendarEvent[]; timeZone: string; now: Date }) {
  return (
    <Card>
      <CardHeader title="Calendar" />
      {events.length === 0 ? (
        <p className="px-5 py-4 text-[14px] text-text-2">No calendar event is linked to this commitment.</p>
      ) : (
        <ul className="divide-y divide-line">
          {events.map((e) => (
            <li key={e.id} className="flex items-start gap-3 px-5 py-3">
              <CalendarCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-ok" strokeWidth={1.7} />
              <div className="min-w-0">
                <p className="text-[14px] font-medium text-ink">{e.title}</p>
                <p data-numeric className="text-[13px] text-text-2">
                  {formatRange(e.start_at, e.end_at, e.timezone || timeZone, now)}
                </p>
                <p className="mt-0.5 text-[12.5px] text-text-3">
                  {e.provider === "GOOGLE" ? "Google Calendar" : "Local calendar (not synced to Google)"}
                  {e.status === "CANCELLED" && " · cancelled"}
                </p>
                {e.url && (
                  <a href={e.url} target="_blank" rel="noreferrer noopener" className="mt-1 inline-flex items-center gap-1 text-[13px] font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
                    Open event <ExternalLink aria-hidden className="size-3" />
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function RunsCard({ runs, now, timeZone }: { runs: AutomationRun[]; now: Date; timeZone: string }) {
  return (
    <Card>
      <CardHeader title="Automation history" description="Workflows that touched this commitment" />
      {runs.length === 0 ? (
        <p className="px-5 py-4 text-[14px] text-text-2">No automation has run for this yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {runs.map((run) => {
            const status = RUN_STATUS[run.status];
            return (
              <li key={run.id} className="px-5 py-3">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-[14px] text-ink">{run.workflow_name}</p>
                  <Badge tone={status.tone} dot>
                    {status.label}
                  </Badge>
                </div>
                <p data-numeric className="mt-0.5 text-[12.5px] text-text-3">
                  {timeAgo(run.started_at, now, timeZone)}
                  {run.duration_ms !== null && ` · ${formatDuration(run.duration_ms)}`}
                </p>
                {run.error && <p className="mt-1 text-[12.5px] text-danger">{run.error}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

export function DetailsCard({ ob, timeZone, now }: { ob: Obligation; timeZone: string; now: Date }) {
  return (
    <Card>
      <CardHeader title="Details" />
      <dl className="divide-y divide-line px-5">
        <Fact label="Created">
          <span data-numeric>{formatMoment(ob.created_at, timeZone, now)}</span>
        </Fact>
        <Fact label="Last changed">
          <span data-numeric>{formatMoment(ob.updated_at, timeZone, now)}</span>
        </Fact>
        {ob.recurrence && <Fact label="Repeats">{ob.recurrence.toLowerCase()}</Fact>}
        {ob.snoozed_until && new Date(ob.snoozed_until) > now && (
          <Fact label="Snoozed until">
            <span data-numeric>{formatMoment(ob.snoozed_until, timeZone, now)}</span>
          </Fact>
        )}
        {ob.completed_at && (
          <Fact label="Completed">
            <span data-numeric>{formatMoment(ob.completed_at, timeZone, now)}</span>
          </Fact>
        )}
      </dl>
    </Card>
  );
}
