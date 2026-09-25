"use client";

import { Workflow } from "lucide-react";
import { useState } from "react";
import { RunList } from "@/components/automations/run-list";
import { ErrorPanel } from "@/components/error-panel";
import { PageHeader } from "@/components/page-header";
import { Badge, StatusIndicator } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/tabs";
import { useClock } from "@/lib/clock";
import { WORKFLOW_BLURB } from "@/lib/labels";
import { useAutomationSummary, useRuns, type WorkflowSummary } from "@/lib/queries";
import { formatDuration, timeAgo } from "@/lib/time";

function SystemStrip() {
  const { status } = useClock();
  if (!status) return <Skeleton className="mb-8 h-20 w-full" />;
  const n8n = status.n8n as { reachable?: boolean };
  const items: { label: string; value: string; tone: "ok" | "warn" | "danger" | "neutral" }[] = [
    { label: "Automation engine", value: n8n.reachable ? "Running" : "Not reachable", tone: n8n.reachable ? "ok" : "danger" },
    {
      label: "Language model",
      value: status.llm_configured ? `${status.llm_provider}${status.llm_model ? ` · ${status.llm_model}` : ""}` : "Not configured",
      tone: status.llm_configured ? "ok" : "warn",
    },
    {
      label: "Email",
      value: status.email_delivery === "smtp" ? "Sent through your mail server" : "Local test inbox only",
      tone: status.email_delivery === "smtp" ? "ok" : "warn",
    },
    { label: "Calendar", value: status.calendar_provider === "google" ? "Google Calendar" : "Local only", tone: status.calendar_provider === "google" ? "ok" : "neutral" },
  ];
  return (
    <Card className="mb-8">
      <dl className="grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
        {items.map((item) => (
          <div key={item.label} className="px-5 py-4">
            <dt className="text-[12.5px] text-text-2">{item.label}</dt>
            <dd className="mt-1.5">
              <StatusIndicator tone={item.tone} label={item.value} className="text-[14px] font-medium text-ink" />
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function WorkflowCard({ w, now, timeZone }: { w: WorkflowSummary; now: Date; timeZone: string }) {
  const rate = w.runs > 0 ? Math.round((w.succeeded / w.runs) * 100) : null;
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-ink">{w.workflow_name}</h3>
        {w.failed > 0 ? (
          <Badge tone="danger" dot>
            {w.failed} failed
          </Badge>
        ) : w.waiting > 0 ? (
          <Badge tone="warn" dot>
            {w.waiting} waiting
          </Badge>
        ) : (
          <Badge tone="ok" dot>
            Healthy
          </Badge>
        )}
      </div>
      <p className="mt-1 text-[13.5px] leading-5 text-text-2">{WORKFLOW_BLURB[w.workflow_key] ?? "Runs as part of the automation."}</p>
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-3">
        <div>
          <dt className="text-[12px] text-text-3">Runs</dt>
          <dd data-numeric className="text-[16px] font-semibold text-ink">
            {w.runs}
          </dd>
        </div>
        <div>
          <dt className="text-[12px] text-text-3">Succeeded</dt>
          <dd data-numeric className="text-[16px] font-semibold text-ink">
            {rate === null ? "-" : `${rate}%`}
          </dd>
        </div>
        <div>
          <dt className="text-[12px] text-text-3">Average</dt>
          <dd data-numeric className="text-[16px] font-semibold text-ink">
            {w.avg_duration_ms === null ? "-" : formatDuration(w.avg_duration_ms)}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-[12.5px] text-text-3">{w.last_started_at ? `Last ran ${timeAgo(w.last_started_at, now, timeZone)}` : "Has not run yet"}</p>
    </Card>
  );
}

function Workflows() {
  const { now, timeZone } = useClock();
  const { data, error, isPending, refetch } = useAutomationSummary();
  if (isPending)
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-44" />
        ))}
      </div>
    );
  if (error || !data) return <ErrorPanel error={error} onRetry={() => void refetch()} title="Couldn't load the workflows" />;
  if (data.workflows.length === 0) {
    return (
      <Card>
        <EmptyState icon={Workflow} title="No automation has run yet" description="Workflows appear here the first time a message arrives or a deadline is checked." />
      </Card>
    );
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {data.workflows.map((w) => (
        <WorkflowCard key={w.workflow_key} w={w} now={now} timeZone={timeZone} />
      ))}
    </div>
  );
}

function Runs({ filter }: { filter: "all" | "failed" | "waiting" }) {
  const { data, error, isPending, refetch } = useRuns({ status: filter === "failed" ? ["FAILED"] : filter === "waiting" ? ["WAITING"] : undefined, limit: 30 });
  if (isPending) return <SkeletonRows rows={4} message="Reading recent runs" />;
  if (error || !data) return <ErrorPanel error={error} onRetry={() => void refetch()} title="Couldn't load the runs" />;
  if (data.items.length === 0) {
    return (
      <EmptyState
        icon={Workflow}
        title={filter === "failed" ? "No failed runs" : filter === "waiting" ? "Nothing is waiting for you" : "No runs yet"}
        description={filter === "failed" ? "When a workflow fails, the reason is recorded here." : filter === "waiting" ? "Workflows that propose an action wait for your approval." : "Runs are recorded as workflows execute."}
      />
    );
  }
  return <RunList runs={data.items} />;
}

export default function AutomationsPage() {
  const [tab, setTab] = useState<"all" | "failed" | "waiting">("all");
  return (
    <>
      <PageHeader title="Automations" description="The workflows that watch your mail and deadlines, and everything they have done. Anything with an outside effect waits for your approval first." />
      <SystemStrip />
      <h2 className="mb-3 px-1 text-[13px] font-semibold text-ink">Workflows · last 7 days</h2>
      <Workflows />
      <Card className="mt-8 overflow-hidden">
        <CardHeader title="Recent runs" description="Each row matches an execution in the automation engine" />
        <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
          <div className="px-3">
            <TabList label="Filter runs">
              <Tab value="all">All</Tab>
              <Tab value="failed">Failed</Tab>
              <Tab value="waiting">Waiting for you</Tab>
            </TabList>
          </div>
          <TabPanel value="all">
            <Runs filter="all" />
          </TabPanel>
          <TabPanel value="failed">
            <Runs filter="failed" />
          </TabPanel>
          <TabPanel value="waiting">
            <Runs filter="waiting" />
          </TabPanel>
        </Tabs>
      </Card>
    </>
  );
}
