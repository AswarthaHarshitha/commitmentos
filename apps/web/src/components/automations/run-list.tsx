"use client";

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { AutomationRun } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useClock } from "@/lib/clock";
import { RUN_STATUS, TRIGGER_LABEL } from "@/lib/labels";
import { formatDuration, formatMoment, timeAgo } from "@/lib/time";

/** Small scalar results only: enough to see what a run did without dumping payloads (which may contain message text). */
function resultFacts(result: AutomationRun["result"]): [string, string][] {
  if (!result) return [];
  return Object.entries(result)
    .filter(([, v]) => ["string", "number", "boolean"].includes(typeof v) && String(v).length <= 120)
    .slice(0, 8)
    .map(([k, v]) => [k.replace(/_/g, " "), String(v)]);
}

function Detail({ run }: { run: AutomationRun }) {
  const { now, timeZone } = useClock();
  const facts = resultFacts(run.result);
  return (
    <div className="space-y-3 text-[13.5px]">
      {run.status === "WAITING" && (
        <p className="text-warn">
          This run is waiting for you to approve or decline what it proposed.{" "}
          <Link href="/inbox?tab=approvals" className="font-medium underline underline-offset-4">
            Open the inbox
          </Link>
        </p>
      )}
      {run.error && (
        <p className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-danger">
          {run.error_node && <span className="font-medium">{run.error_node}: </span>}
          {run.error}
        </p>
      )}
      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[9rem_1fr]">
        <dt className="text-text-3">Started</dt>
        <dd data-numeric className="text-ink">
          {formatMoment(run.started_at, timeZone, now)}
        </dd>
        {run.finished_at && (
          <>
            <dt className="text-text-3">Finished</dt>
            <dd data-numeric className="text-ink">
              {formatMoment(run.finished_at, timeZone, now)}
            </dd>
          </>
        )}
        <dt className="text-text-3">Attempt</dt>
        <dd data-numeric className="text-ink">
          {run.attempt}
        </dd>
        {run.n8n_execution_id && (
          <>
            <dt className="text-text-3">n8n execution</dt>
            <dd data-numeric className="font-mono text-[12.5px] text-ink">
              {run.n8n_execution_id}
            </dd>
          </>
        )}
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-3">{k}</dt>
            <dd className="break-words text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {run.obligation_id && (
        <Link href={`/obligations/${run.obligation_id}`} className="inline-block font-medium text-text-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
          Open the commitment
        </Link>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: AutomationRun["status"] }) {
  const s = RUN_STATUS[status];
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

/** Desktop: a real table with expandable rows. Small screens: the same runs as stacked rows (a table cannot be read at 390px). */
export function RunList({ runs }: { runs: AutomationRun[] }) {
  const { now, timeZone } = useClock();
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (id: string) => setOpen((current) => (current === id ? null : id));

  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-[14px]">
          <caption className="sr-only">Recent automation runs</caption>
          <thead>
            <tr className="border-b border-line text-[12.5px] font-medium text-text-2">
              <th scope="col" className="px-5 py-2.5 font-medium">
                Workflow
              </th>
              <th scope="col" className="px-3 py-2.5 font-medium">
                Status
              </th>
              <th scope="col" className="px-3 py-2.5 font-medium">
                Trigger
              </th>
              <th scope="col" className="px-3 py-2.5 font-medium">
                Started
              </th>
              <th scope="col" className="px-5 py-2.5 text-right font-medium">
                Took
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const expanded = open === run.id;
              return [
                <tr key={run.id} className={cn("border-b border-line transition-colors hover:bg-surface-2/70", expanded && "bg-surface-2/70 border-b-transparent")}>
                  <td className="px-5 py-3">
                    <button type="button" aria-expanded={expanded} onClick={() => toggle(run.id)} className="inline-flex items-center gap-1.5 rounded text-left font-medium text-ink">
                      <ChevronRight aria-hidden className={cn("size-4 text-text-3 transition-transform duration-150", expanded && "rotate-90")} strokeWidth={1.8} />
                      {run.workflow_name}
                    </button>
                  </td>
                  <td className="px-3 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-3 py-3 text-text-2">{TRIGGER_LABEL[run.trigger] ?? run.trigger}</td>
                  <td data-numeric className="px-3 py-3 text-text-2">
                    {timeAgo(run.started_at, now, timeZone)}
                  </td>
                  <td data-numeric className="px-5 py-3 text-right text-text-2">
                    {run.duration_ms !== null ? formatDuration(run.duration_ms) : "-"}
                  </td>
                </tr>,
                expanded && (
                  <tr key={`${run.id}-detail`} className="border-b border-line bg-surface-2/70">
                    <td colSpan={5} className="px-5 pb-4 pl-11">
                      <Detail run={run} />
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>

      <ul className="divide-y divide-line md:hidden">
        {runs.map((run) => {
          const expanded = open === run.id;
          return (
            <li key={run.id}>
              <button type="button" aria-expanded={expanded} onClick={() => toggle(run.id)} className="flex w-full items-start justify-between gap-3 px-4 py-3.5 text-left">
                <span className="min-w-0">
                  <span className="block text-[14px] font-medium text-ink">{run.workflow_name}</span>
                  <span data-numeric className="mt-0.5 block text-[12.5px] text-text-3">
                    {timeAgo(run.started_at, now, timeZone)}
                    {run.duration_ms !== null && ` · ${formatDuration(run.duration_ms)}`} · {TRIGGER_LABEL[run.trigger] ?? run.trigger}
                  </span>
                </span>
                <StatusBadge status={run.status} />
              </button>
              {expanded && (
                <div className="bg-surface-2/70 px-4 pb-4">
                  <Detail run={run} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
