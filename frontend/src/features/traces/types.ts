import type { Page, TraceDetail, TraceSummary } from "../../types";

export type Tone = "good" | "warn" | "bad" | "";
export interface ProcessSummary {
  business_status: string;
  reply_status: string;
  outcome: string;
  repaired: boolean;
  has_issues: boolean;
  model_calls: number | null;
  tool_calls: number | null;
  background_model_calls: number;
  client_status: string;
  delivery_status: string;
  coverage: string;
}
export interface ProcessNode {
  id: string;
  title: string;
  kind: string;
  parent_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  status: string;
  summary: string;
  input: unknown;
  output: unknown;
  evidence_refs: string[];
  issues: Array<Record<string, unknown>>;
  raw: unknown;
}
export interface ProcessMessage {
  id?: string;
  participant?: string;
  kind?: string;
  text?: string;
  notice_text?: string;
  requires_response?: boolean;
  card?: { card_type?: string; data?: unknown; status?: string };
}
export interface CallRow extends TraceSummary {
  nickname: string | null;
  input_text: string | null;
  image_count: number;
  process: ProcessSummary;
}
export interface CallDetail extends TraceDetail {
  process: {
    schema_version: string;
    summary: ProcessSummary;
    nodes: ProcessNode[];
    sources: Array<{ id: string; node_id: string | null }>;
    artifacts: Array<{
      artifact_id: string;
      artifact_type: string;
      payload: unknown;
    }>;
    messages: ProcessMessage[];
    background_jobs: Array<{
      id: string;
      kind: string;
      status: string;
      attempt_count: number;
      error_code: string | null;
      created_at: string;
      completed_at: string | null;
    }>;
    background_calls: Array<{
      id: string;
      model: string;
      status: string;
      duration_ms: number | null;
      error_type: string | null;
      total_tokens: number | null;
    }>;
  };
}
export type CallPage = Page<CallRow>;
