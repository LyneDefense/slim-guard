import { request } from "../../api";

export type Totals = {
  calls: number; invocations: number; input_tokens: number; output_tokens: number;
  total_tokens: number; unknown_calls: number; failed_calls: number; running_calls: number;
};
export type UsageRow = Totals & { id?: string; user_id?: string; started_at?: string; status?: string; category?: string; exchanges?: number };
export type Page = { items: UsageRow[]; total: number; limit: number; offset: number };
export type ModelCall = {
  id: string; model: string; purpose: string; component: string; agent_role: string | null;
  invocation_id: string | null; parent_invocation_id: string | null; tool_call_id: string | null;
  status: string; error_type: string | null; started_at: string; duration_ms: number | null;
  input_tokens: number | null; output_tokens: number | null; total_tokens: number | null;
};
export type Detail = {
  summary: Totals; executions: number; agents: (Totals & { agent_role: string | null; executions?: number })[];
  groups: (Totals & { agent_role: string | null; component: string; execution_kind: string })[];
  items: ModelCall[]; total: number; limit: number; offset: number; coverage: string; trace_id: string | null;
};
export const usageApi = {
  list: (path: string, params: URLSearchParams) => request<Page>(`/usage/${path}?${params}`),
  detail: (path: string, offset: number) => request<Detail>(`/usage/${path}?offset=${offset}&limit=20`),
};
