import type {
  MemoryRecord,
  Page,
  TraceDetail,
  DishRecognitionCorrectionInput,
  TraceListFilters,
  TraceSummary,
  TraceWorkflowReviewMetrics,
  UserDetail,
  UserListItem,
  NutritionDashboard,
  NutritionEvaluationCase,
  NutritionEvaluationDataset,
  NutritionEvaluationRun,
  NutritionJob,
  NutritionJobEvent,
  NutritionRelease,
  NutritionRetrievalRun,
  NutritionRuntime,
  NutritionSourceChunk,
  NutritionSourceDetail,
  NutritionSourceSection,
  NutritionSourceSummary,
  NutritionReviewDecision,
  NutritionReviewType,
} from "./types";

export type AdminSession = {
  username: string;
  expires_at: number;
};

export class UnauthorizedError extends Error {}

export async function request<T>(
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

async function requestBlob(path: string): Promise<Blob> {
  const response = await fetch(`/api/admin${path}`, {
    credentials: "same-origin",
    headers: { Accept: "application/octet-stream" },
  });
  if (response.status === 401) {
    window.location.assign("/admin/login");
    throw new UnauthorizedError("登录已失效，请重新登录");
  }
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  return response.blob();
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
  appendDishRecognitionCorrection: (
    userId: string,
    traceId: string,
    artifactId: string,
    input: DishRecognitionCorrectionInput,
  ) => request(
    `/users/${encodeURIComponent(userId)}/traces/${encodeURIComponent(traceId)}`
      + `/dish-recognition-corrections/${encodeURIComponent(artifactId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify(input),
    },
  ),
  workflowMetrics: (windowDays = 7) =>
    request<TraceWorkflowReviewMetrics>(
      `/metrics/workflows?window_days=${encodeURIComponent(windowDays)}`,
    ),
  nutritionDashboard: () => request<NutritionDashboard>("/nutrition-knowledge/dashboard"),
  nutritionSources: (filters: { status?: string; search?: string; offset?: number } = {}) => {
    const query = new URLSearchParams({
      limit: "30",
      offset: String(filters.offset ?? 0),
    });
    if (filters.status) query.set("status", filters.status);
    if (filters.search) query.set("search", filters.search);
    return request<Page<NutritionSourceSummary>>(
      `/nutrition-knowledge/sources?${query.toString()}`,
    );
  },
  nutritionSource: (sourceId: string) =>
    request<NutritionSourceDetail>(
      `/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}`,
    ),
  nutritionSourceSections: (sourceId: string, offset = 0) =>
    request<Page<NutritionSourceSection>>(
      `/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}/sections?limit=20&offset=${offset}`,
    ),
  nutritionSourceChunks: (sourceId: string, kind = "retrieval_child", offset = 0) => {
    const query = new URLSearchParams({ limit: "20", offset: String(offset) });
    if (kind) query.set("kind", kind);
    return request<Page<NutritionSourceChunk>>(
      `/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}/chunks?${query.toString()}`,
    );
  },
  importNutritionFile: (metadata: Record<string, unknown>, file: File) => {
    const form = new FormData();
    form.set("metadata", JSON.stringify(metadata));
    form.set("file", file);
    return request<{ job: NutritionJob }>("/nutrition-knowledge/sources/imports", {
      method: "POST",
      headers: { "X-SlimGuard-CSRF": "1" },
      body: form,
    });
  },
  importNutritionTextOrUrl: (input: Record<string, unknown>) =>
    request<{ job: NutritionJob }>("/nutrition-knowledge/sources/imports", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify(input),
    }),
  downloadNutritionSource: (sourceId: string) =>
    requestBlob(`/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}/asset`),
  reviewNutritionSource: (
    sourceId: string,
    reviewType: NutritionReviewType,
    decision: NutritionReviewDecision,
    reason: string | null,
  ) => request<NutritionSourceDetail>(
    `/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}/reviews`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify({
        review_type: reviewType,
        decision,
        reason,
        attestations: { reviewed_in_admin_console: true },
      }),
    },
  ),
  retireNutritionSource: (sourceId: string, reason: string) =>
    request<{ retired: boolean }>(
      `/nutrition-knowledge/sources/${encodeURIComponent(sourceId)}/retire`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
        body: JSON.stringify({ reason }),
      },
    ),
  nutritionJobs: () =>
    request<{ items: NutritionJob[]; limit: number; offset: number }>(
      "/nutrition-knowledge/jobs?limit=100&offset=0",
    ),
  nutritionJob: (jobId: string) =>
    request<{ job: NutritionJob; events: NutritionJobEvent[] }>(
      `/nutrition-knowledge/jobs/${encodeURIComponent(jobId)}`,
    ),
  retryNutritionJob: (jobId: string) =>
    request<{ job: NutritionJob }>(
      `/nutrition-knowledge/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST", headers: { "X-SlimGuard-CSRF": "1" } },
    ),
  cancelNutritionJob: (jobId: string) =>
    request<{ job: NutritionJob }>(
      `/nutrition-knowledge/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST", headers: { "X-SlimGuard-CSRF": "1" } },
    ),
  nutritionReleases: () =>
    request<{ items: NutritionRelease[] }>("/nutrition-knowledge/releases"),
  createNutritionRelease: (version: string, sourceIds: string[]) =>
    request<{ release: NutritionRelease }>("/nutrition-knowledge/releases", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-SlimGuard-CSRF": "1",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ version, source_ids: sourceIds }),
    }),
  evaluateNutritionRelease: (releaseId: string, datasetId: string) =>
    request<{ evaluation_run_id: string; job: NutritionJob }>(
      `/nutrition-knowledge/releases/${encodeURIComponent(releaseId)}/evaluate`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-SlimGuard-CSRF": "1",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ dataset_id: datasetId }),
      },
    ),
  reviewNutritionRelease: (
    releaseId: string,
    decision: "approve" | "reject",
    reason: string | null,
  ) => request<{ release: NutritionRelease }>(
    `/nutrition-knowledge/releases/${encodeURIComponent(releaseId)}/reviews`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify({ decision, reason }),
    },
  ),
  activateNutritionRelease: (releaseId: string, reason: string) =>
    request<{ runtime: NutritionRuntime }>(
      `/nutrition-knowledge/releases/${encodeURIComponent(releaseId)}/activate`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-SlimGuard-CSRF": "1",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ reason }),
      },
    ),
  nutritionRuntime: () =>
    request<{ runtime: NutritionRuntime }>("/nutrition-knowledge/runtime"),
  rollbackNutritionRuntime: (releaseId: string, reason: string) =>
    request<{ runtime: NutritionRuntime }>("/nutrition-knowledge/runtime/rollback", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-SlimGuard-CSRF": "1",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ release_id: releaseId, reason }),
    }),
  runNutritionRetrievalLab: (input: {
    query: string;
    release_id: string | null;
    max_results: number;
    metadata_filter: Record<string, unknown>;
  }) => request<{
    corpus_status: string;
    query_summary: string;
    retrieval_run_id: string | null;
  }>("/nutrition-knowledge/retrieval-lab/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
    body: JSON.stringify(input),
  }),
  nutritionRetrievalLabRun: (runId: string) =>
    request<NutritionRetrievalRun>(
      `/nutrition-knowledge/retrieval-lab/runs/${encodeURIComponent(runId)}`,
    ),
  appendNutritionLabEvaluationCase: (
    runId: string,
    input: {
      base_dataset_id: string | null;
      dataset_version: string;
      case_key: string;
      expected_source_keys: string[];
      expected_chunk_concepts: string[];
      forbidden_source_keys: string[];
      expected_outcome: "evidence" | "insufficient";
    },
  ) => request<{ dataset: NutritionEvaluationDataset }>(
    `/nutrition-knowledge/retrieval-lab/runs/${encodeURIComponent(runId)}/evaluation-cases`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify(input),
    },
  ),
  nutritionEvaluationDatasets: () =>
    request<{ items: NutritionEvaluationDataset[] }>(
      "/nutrition-knowledge/evaluation-datasets",
    ),
  createNutritionEvaluationDataset: (
    version: string,
    cases: Array<Omit<NutritionEvaluationCase, "id">>,
  ) => request<{ dataset: NutritionEvaluationDataset }>(
    "/nutrition-knowledge/evaluation-datasets",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SlimGuard-CSRF": "1" },
      body: JSON.stringify({ version, cases }),
    },
  ),
  nutritionEvaluationRuns: () =>
    request<{ items: NutritionEvaluationRun[] }>(
      "/nutrition-knowledge/evaluation-runs?limit=100",
    ),
  nutritionEvaluationRun: (runId: string) =>
    request<NutritionEvaluationRun>(
      `/nutrition-knowledge/evaluation-runs/${encodeURIComponent(runId)}`,
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
