import type { components } from "./api-types";

type S = components["schemas"];
export type Obligation = S["ObligationOut"];
export type ObligationDetail = S["ObligationDetail"];
export type ObligationList = S["ObligationList"];
export type Dashboard = S["DashboardOut"];
export type SystemStatus = S["SystemStatus"];
export type Candidate = S["CandidateOut"];
export type Approval = S["ApprovalOut"];
export type AppNotification = S["NotificationOut"];
export type AuditEvent = S["AuditEventOut"];
export type AuditList = S["AuditList"];
export type AutomationRun = S["AutomationRunOut"];
export type AutomationRunList = S["AutomationRunList"];
export type TimelineEntry = S["TimelineEntry"];
export type User = S["UserOut"];
export type Source = S["SourceOut"];
export type CalendarEvent = S["CalendarEventOut"];
export type SuggestedAction = S["SuggestedAction"];
export type Understanding = S["Understanding"];
export type ImportOutcome = S["ImportOutcome"];

export type Status = S["ObligationStatus"];
export type ObligationType = S["ObligationType"];
export type Priority = S["Priority"];
export type RunStatus = S["RunStatus"];
export type ApprovalAction = S["ApprovalAction"];
export type ApprovalStatus = S["ApprovalStatus"];
export type DuePrecision = S["DuePrecision"];

/** Raised for any non-2xx answer. `code` is the API's stable machine-readable error code. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const UNAUTHORIZED_EVENT = "cos:unauthorized";
const AUTH_PATHS = ["/auth/login", "/auth/register", "/auth/me"];

function errorFrom(status: number, body: unknown): ApiError {
  const payload = (body ?? {}) as { detail?: unknown; code?: unknown };
  let message = "Something went wrong. Please try again.";
  if (typeof payload.detail === "string") message = payload.detail;
  else if (Array.isArray(payload.detail)) {
    const first = payload.detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    const field = Array.isArray(first?.loc) ? String(first?.loc?.filter((p) => p !== "body").join(".") ?? "") : "";
    message = first?.msg ? `${field ? `${field}: ` : ""}${first.msg}` : "Some of the values are not valid.";
  } else if (status >= 500) message = "The service is having trouble. Please try again in a moment.";
  return new ApiError(status, typeof payload.code === "string" ? payload.code : null, message);
}

export function qs(params?: Record<string, string | number | boolean | null | undefined | (string | number)[]>): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => usp.append(key, String(v)));
    else usp.set(key, String(value));
  }
  const text = usp.toString();
  return text ? `?${text}` : "";
}

async function request<T>(method: string, path: string, json?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(json !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: json !== undefined ? JSON.stringify(json) : undefined,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK", "Could not reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined" && !AUTH_PATHS.some((p) => path.startsWith(p))) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw errorFrom(response.status, body);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T = void>(path: string, json?: unknown) => request<T>("POST", path, json ?? {}),
  patch: <T>(path: string, json: unknown) => request<T>("PATCH", path, json),
};

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}
