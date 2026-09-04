export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserListItem {
  id: string;
  user_ref: string;
  external_refs: string[];
  nickname: string | null;
  gender: number | null;
  first_seen_at: string;
  last_seen_at: string;
  last_generation_status: string | null;
  last_delivery_status: string | null;
  trace_count: number;
  issue_count: number;
  last_trace_at: string | null;
}

export interface UserDetail {
  id: string;
  user_ref: string;
  nickname: string | null;
  gender: number | null;
  first_seen_at: string;
  last_seen_at: string;
  identities: Array<{
    channel_id: string;
    external_ref: string;
    profile_status: string;
    profile_synced_at: string | null;
  }>;
  counts: Record<string, number>;
  active_handoff: {
    id: string;
    objective: string;
    unresolved: unknown;
    expires_at: string;
  } | null;
  routine: Record<string, string | null> | null;
}

export interface TraceSummary {
  id: string;
  user_id: string;
  trigger_type: string;
  channel_id: string | null;
  inbound_msgid: string | null;
  agent_turn_id: string | null;
  agent_version_id: string | null;
  reply_kind: string;
  generation_status: string;
  delivery_status: string;
  failure_code: string | null;
  error_detail: string | null;
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

export type AgentRole =
  | "orchestrator"
  | "nutrition_expert"
  | "response_style"
  | "response_reviewer";

export type AgentInvocationStatus = "succeeded" | "degraded" | "failed";

export interface InvocationStartedDetails {
  invocation_id: string;
  agent_role: AgentRole;
  agent_version: string;
  attempt: number;
  parent_invocation_id: string | null;
  input_artifact_ids: string[];
  allowed_tool_names: string[];
  privacy_scopes: string[];
  reason_summary: string;
  started_at: string;
}

export interface InvocationResultDetails {
  invocation_id: string;
  status: AgentInvocationStatus;
  output_artifact_id: string | null;
  model_call_count: number;
  tool_call_count: number;
  total_token_count: number;
  failure_code: string | null;
  completed_at: string;
}

export interface ArtifactCreatedDetails {
  artifact_id: string;
  artifact_type: string;
  producer_role: string;
  schema_version: string;
  parent_artifact_ids: string[];
  payload_sha256: string;
}

export interface WorkflowTransitionDetails {
  from_node: string;
  to_node: string;
  transition_type: string;
  reason_code: string;
  attempt: number;
}

export interface ResponseAdoptedDetails {
  artifact_id: string;
  mode: string;
  final: boolean;
}

export interface ResponseDegradedDetails {
  artifact_id: string | null;
  reason_code: string;
  fallback_type: string;
}

export type MultiAgentTraceOperation =
  | "invocation_started"
  | "invocation_result"
  | "artifact_created"
  | "workflow_transition"
  | "response_adopted"
  | "response_degraded";

export interface TimelineEvent<TDetails = unknown> {
  event_type: string;
  id: string;
  parent_span_id?: string | null;
  sequence: number;
  component: string;
  operation: string;
  status: string;
  details: TDetails;
  error_code?: string | null;
  error_detail?: string | null;
  redacted?: boolean;
  redaction_policy?: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  presentation: {
    stage: string;
    title: string;
    summary: string;
    facts: Array<{ label: string; value: string }>;
  };
}

export type MultiAgentTimelineEvent =
  | (TimelineEvent<InvocationStartedDetails> & { operation: "invocation_started" })
  | (TimelineEvent<InvocationResultDetails> & { operation: "invocation_result" })
  | (TimelineEvent<ArtifactCreatedDetails> & { operation: "artifact_created" })
  | (TimelineEvent<WorkflowTransitionDetails> & { operation: "workflow_transition" })
  | (TimelineEvent<ResponseAdoptedDetails> & { operation: "response_adopted" })
  | (TimelineEvent<ResponseDegradedDetails> & { operation: "response_degraded" });

export interface TraceDetail {
  trace: TraceSummary;
  turn: Record<string, unknown> | null;
  agent: {
    id: string;
    model_provider: string | null;
    text_model: string | null;
    vision_model: string | null;
    system_prompt_version: string | null;
    context_policy_version: string | null;
    memory_policy_version: string | null;
    safety_policy_version: string | null;
    code_revision: string;
    tool_count: number;
  } | null;
  timeline: TimelineEvent[];
  execution_summary: {
    architecture: string;
    model_call_count: number;
    tool_call_count: number;
    observation_count: number;
    context_snapshot_count: number;
    memory_ingestion_count: number;
    memory_recall_count: number;
  };
  context_sources: Array<{
    kind: string;
    title: string;
    retention: string;
    description: string;
    items: Array<{ label: string; value: string; detail: string }>;
  }>;
  tool_executions: Array<Record<string, unknown>>;
  output: {
    kind: string;
    content: string;
    status: string;
    platform_msgid: string;
    last_error: string | null;
    attempt_started_at: string | null;
    completed_at: string | null;
  } | null;
  privacy: {
    contains_sensitive_health_data: boolean;
    redacted_item_count: number;
  };
}

export interface MemoryRecord {
  id: string;
  kind: string;
  memory_key: string;
  value: Record<string, unknown> | null;
  status: string;
  assertion: string;
  sensitivity: string;
  source_turn_id: string;
  source_item_id: string;
  evidence_item_id: string | null;
  valid_from: string;
  expires_at: string | null;
  review_after: string | null;
  ended_at: string | null;
  semantic_index: {
    provider: string;
    operation?: string;
    status: string;
    attempt_count?: number;
    error_code?: string | null;
    error_detail?: string | null;
    updated_at?: string;
  };
}
