"use client";

import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  api,
  qs,
  type Approval,
  type AppNotification,
  type AuditList,
  type AutomationRunList,
  type Candidate,
  type Dashboard,
  type Obligation,
  type ImportOutcome,
  type ObligationDetail,
  type ObligationList,
  type Status,
  type SystemStatus,
  type TimelineEntry,
  type User,
} from "./api";
import { startSettling } from "./settling";

const keys = {
  me: ["me"] as const,
  status: ["system-status"] as const,
  dashboard: ["dashboard"] as const,
  obligations: (params: object) => ["obligations", params] as const,
  obligation: (id: string) => ["obligation", id] as const,
  timeline: (id: string) => ["timeline", id] as const,
  candidates: ["candidates"] as const,
  approvals: (status?: string) => ["approvals", status ?? "all"] as const,
  notifications: ["notifications"] as const,
  audit: (params: object) => ["audit", params] as const,
  runs: (params: object) => ["runs", params] as const,
  automation: ["automation-summary"] as const,
};

// ------------------------------------------------------------------------------------------------ reads
export const useMe = () => useQuery({ queryKey: keys.me, queryFn: ({ signal }) => api.get<User>("/auth/me", signal), retry: false, staleTime: 5 * 60_000 });

export const useSystemStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: ({ signal }) => api.get<SystemStatus>("/system/status", signal), refetchInterval: 30_000, staleTime: 15_000 });

export const useDashboard = () =>
  useQuery({ queryKey: keys.dashboard, queryFn: ({ signal }) => api.get<Dashboard>("/dashboard", signal), refetchInterval: 60_000, staleTime: 20_000 });

export interface ObligationParams {
  view?: "inbox" | "active" | "review" | "overdue" | "closed" | "upcoming" | "all";
  status?: Status[];
  q?: string;
  sort?: "due" | "created" | "priority";
  limit?: number;
  offset?: number;
}

export const useObligations = (params: ObligationParams, options: { enabled?: boolean } = {}) =>
  useQuery({
    queryKey: keys.obligations(params),
    queryFn: ({ signal }) => api.get<ObligationList>(`/obligations${qs({ limit: 100, ...params })}`, signal),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    enabled: options.enabled ?? true,
  });

export const useObligation = (id: string, options: { enabled?: boolean } = {}) =>
  useQuery({
    queryKey: keys.obligation(id),
    queryFn: ({ signal }) => api.get<ObligationDetail>(`/obligations/${id}`, signal),
    retry: (n, error) => (error as { status?: number }).status !== 404 && n < 2,
    enabled: options.enabled ?? true,
  });

export const useTimeline = (id: string) => useQuery({ queryKey: keys.timeline(id), queryFn: ({ signal }) => api.get<TimelineEntry[]>(`/obligations/${id}/timeline`, signal) });

export const useCandidates = () =>
  useQuery({ queryKey: keys.candidates, queryFn: ({ signal }) => api.get<Candidate[]>(`/candidates${qs({ status: "PENDING", limit: 50 })}`, signal), staleTime: 15_000 });

export const useApprovals = (status?: string) =>
  useQuery({ queryKey: keys.approvals(status), queryFn: ({ signal }) => api.get<Approval[]>(`/approvals${qs({ status, limit: 100 })}`, signal), staleTime: 15_000 });

export const useNotifications = () =>
  useQuery({ queryKey: keys.notifications, queryFn: ({ signal }) => api.get<{ items: AppNotification[]; unread: number }>(`/notifications${qs({ limit: 30 })}`, signal), refetchInterval: 45_000 });

export const useAutomationSummary = () =>
  useQuery({ queryKey: keys.automation, queryFn: ({ signal }) => api.get<{ since: string; workflows: WorkflowSummary[] }>("/automation/summary?hours=168", signal), refetchInterval: 30_000 });

export interface WorkflowSummary {
  workflow_key: string;
  workflow_name: string;
  runs: number;
  succeeded: number;
  failed: number;
  waiting: number;
  avg_duration_ms: number | null;
  last_started_at: string | null;
}

export const useRuns = (params: { workflow_key?: string; status?: string[]; limit?: number; offset?: number }) =>
  useQuery({
    queryKey: keys.runs(params),
    queryFn: ({ signal }) => api.get<AutomationRunList>(`/automation/runs${qs({ limit: 50, ...params })}`, signal),
    placeholderData: keepPreviousData,
    refetchInterval: 20_000,
  });

export const useAudit = (params: { event_type?: string; obligation_id?: string }) =>
  useInfiniteQuery({
    queryKey: keys.audit(params),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => api.get<AuditList>(`/audit${qs({ limit: 40, before: pageParam, ...params })}`, signal),
    getNextPageParam: (last) => last.next_before ?? undefined,
  });

// ------------------------------------------------------------------------------------------------ writes
export function invalidateAfterChange(client: QueryClient, id?: string) {
  for (const key of [keys.dashboard, ["obligations"], keys.candidates, ["approvals"], keys.notifications, keys.automation, ["audit"], ["runs"]]) {
    void client.invalidateQueries({ queryKey: key });
  }
  if (id) {
    void client.invalidateQueries({ queryKey: keys.obligation(id) });
    void client.invalidateQueries({ queryKey: keys.timeline(id) });
  }
}

/**
 * Every command on a commitment: the server decides, the client refreshes what it may have changed.
 * `settleMs` holds the refresh back briefly so a row that was just completed can be seen as done before it leaves its list.
 */
export function useObligationAction(action: "complete" | "dismiss" | "approve" | "reopen" | "snooze" | "schedule" | "follow-up", options: { settleMs?: number } = {}) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: Record<string, unknown> }) => api.post<unknown>(`/obligations/${id}/${action}`, body),
    onSuccess: (_data, { id }) => {
      if (options.settleMs) startSettling(id, options.settleMs, () => invalidateAfterChange(client, id));
      else invalidateAfterChange(client, id);
    },
  });
}

export function useEditObligation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) => api.patch<Obligation>(`/obligations/${id}`, patch),
    onSuccess: (_data, { id }) => invalidateAfterChange(client, id),
  });
}

export function useCreateObligation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<Obligation>("/obligations", body),
    onSuccess: () => invalidateAfterChange(client),
  });
}

export function useApprovalDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) => api.post<Approval>(`/approvals/${id}/${decision}`),
    onSuccess: (data) => invalidateAfterChange(client, data.obligation_id),
  });
}

export function useEditApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Record<string, unknown> }) => api.patch<Approval>(`/approvals/${id}`, patch),
    onSuccess: (data) => invalidateAfterChange(client, data.obligation_id),
  });
}

export function useCandidateDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "promote" | "discard" }) => api.post<Obligation | undefined>(`/candidates/${id}/${decision}`),
    onSuccess: () => invalidateAfterChange(client),
  });
}

export function useMarkNotificationsRead() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id?: string) => (id ? api.post(`/notifications/${id}/read`) : api.post("/notifications/read-all")),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useAuth() {
  const client = useQueryClient();
  const login = useMutation({
    mutationFn: (body: { email: string; password: string }) => api.post<User>("/auth/login", body),
    onSuccess: (user) => client.setQueryData(keys.me, user),
  });
  const register = useMutation({
    mutationFn: (body: { email: string; password: string; display_name: string; timezone: string }) => api.post<User>("/auth/register", body),
    onSuccess: (user) => client.setQueryData(keys.me, user),
  });
  const logout = useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSuccess: () => client.clear(),
  });
  return { login, register, logout };
}

export function useUpdateProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { display_name?: string; timezone?: string; preferences?: Record<string, unknown> }) => api.patch<User>("/auth/me", body),
    onSuccess: (user) => {
      client.setQueryData(keys.me, user);
      void client.invalidateQueries({ queryKey: keys.dashboard });
    },
  });
}

/**
 * Where an imported email has got to. Polls until the detection workflow has reported (or failed); `since` keeps the answer of an
 * earlier, failed attempt from being mistaken for the current one.
 */
export const useImportOutcome = (externalId: string | null, since: string | null) =>
  useQuery({
    queryKey: ["import", externalId, since],
    enabled: externalId !== null,
    queryFn: ({ signal }) => api.get<ImportOutcome>(`/messages/import/${externalId}${qs({ since })}`, signal),
    refetchInterval: (query) => (query.state.data?.status === "processing" || !query.state.data ? 1500 : false),
    gcTime: 0,
  });

export function useImportEmail() {
  return useMutation({
    mutationFn: (body: { body: string; subject?: string; sender?: string; received_at?: string }) => api.post<ImportOutcome>("/messages/import", body),
  });
}
