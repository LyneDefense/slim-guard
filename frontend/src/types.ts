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

export interface TimelineEvent {
  event_type: string;
  id: string;
  parent_span_id?: string | null;
  sequence: number;
  component: string;
  operation: string;
  status: string;
  details: unknown;
  error_code?: string | null;
  error_detail?: string | null;
  redacted?: boolean;
  redaction_policy?: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface TraceDetail {
  trace: TraceSummary;
  turn: Record<string, unknown> | null;
  timeline: TimelineEvent[];
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
