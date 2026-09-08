import type {
  MemoryRecord,
  Page,
  StyleABCaseDetail,
  StyleABCaseSummary,
  StyleABHumanReview,
  StyleABReviewInput,
  StyleABStatistics,
  StyleCorrectionFeedback,
  StyleCorrectionFeedbackInput,
  StyleFeedbackContext,
  TraceDetail,
  TraceListFilters,
  TraceSummary,
  TraceWorkflowReviewMetrics,
  UserDetail,
  UserListItem,
} from "./types";

export type AdminSession = {
  username: string;
  expires_at: number;
};

export class UnauthorizedError extends Error {}

async function request<T>(
  path: string,
  init: RequestInit = {},
  redirectOnUnauthorized = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`/api/admin${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try {
      const payload = JSON.parse(body) as { detail?: string };
      detail = payload.detail ?? body;
    } catch {
      // Keep the plain response body when the backend did not return JSON.
    }
    if (response.status === 401) {
      if (redirectOnUnauthorized && window.location.pathname !== "/admin/login") {
        window.location.assign("/admin/login");
      }
      throw new UnauthorizedError("登录已失效，请重新登录");
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  session: () => request<AdminSession>("/session", {}, false),
  login: (username: string, password: string) =>
    request<AdminSession>(
      "/auth/login",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      },
      false,
    ),
  logout: () => request<{ logged_out: boolean }>("/auth/logout", { method: "POST" }, false),
  users: (search = "", offset = 0) =>
    request<Page<UserListItem>>(
      `/users?search=${encodeURIComponent(search)}&limit=30&offset=${offset}`,
    ),
  user: (userId: string) => request<UserDetail>(`/users/${encodeURIComponent(userId)}`),
  traces: (userId: string, offset = 0, filters: TraceListFilters = {}) => {
    const query = new URLSearchParams({ limit: "30", offset: String(offset) });
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, value);
    }
    return request<Page<TraceSummary>>(
      `/users/${encodeURIComponent(userId)}/traces?${query.toString()}`,
    );
  },
  trace: (userId: string, traceId: string) =>
    request<TraceDetail>(
      `/users/${encodeURIComponent(userId)}/traces/${encodeURIComponent(traceId)}`,
    ),
  workflowMetrics: (windowDays = 7) =>
    request<TraceWorkflowReviewMetrics>(
      `/metrics/workflows?window_days=${encodeURIComponent(windowDays)}`,
    ),
  styleABCases: (
    offset = 0,
    filters: {
      candidate_profile_version?: string;
      communication_act?: string;
      decision?: string;
    } = {},
  ) => {
    const query = new URLSearchParams({ limit: "30", offset: String(offset) });
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, value);
    }
    return request<Page<StyleABCaseSummary>>(`/style-ab/cases?${query.toString()}`);
  },
  styleABCase: (caseId: string) =>
    request<StyleABCaseDetail>(`/style-ab/cases/${encodeURIComponent(caseId)}`),
  styleABStatistics: (candidateProfileVersion = "") => {
    const query = new URLSearchParams();
    if (candidateProfileVersion) {
      query.set("candidate_profile_version", candidateProfileVersion);
    }
    return request<StyleABStatistics>(
      `/style-ab/statistics${query.size ? `?${query.toString()}` : ""}`,
    );
  },
  reviewStyleABCase: (caseId: string, input: StyleABReviewInput) =>
    request<StyleABHumanReview>(
      `/style-ab/cases/${encodeURIComponent(caseId)}/reviews`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-SlimGuard-CSRF": "1",
        },
        body: JSON.stringify(input),
      },
    ),
  styleFeedbackContext: () => request<StyleFeedbackContext>("/style-feedback/context"),
  styleFeedback: (
    offset = 0,
    filters: { profile_version?: string; communication_act?: string } = {},
  ) => {
    const query = new URLSearchParams({ limit: "30", offset: String(offset) });
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, value);
    }
    return request<Page<StyleCorrectionFeedback>>(`/style-feedback?${query.toString()}`);
  },
  appendStyleFeedback: (input: StyleCorrectionFeedbackInput) =>
    request<StyleCorrectionFeedback>(
      "/style-feedback",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-SlimGuard-CSRF": "1",
        },
        body: JSON.stringify(input),
      },
    ),
  memories: (userId: string) =>
    request<MemoryRecord[]>(`/users/${encodeURIComponent(userId)}/memories`),
  records: (userId: string) =>
    request<Record<string, Array<Record<string, unknown>>>>(
      `/users/${encodeURIComponent(userId)}/records`,
    ),
  routines: (userId: string) =>
    request<{ preference: Record<string, unknown> | null; jobs: Array<Record<string, unknown>> }>(
      `/users/${encodeURIComponent(userId)}/routines`,
    ),
};
