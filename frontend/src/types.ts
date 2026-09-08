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
  mode?: string | null;
  agent_failure?: boolean | null;
  rag?: boolean | null;
  repair?: boolean | null;
  degraded?: boolean | null;
  graph_version?: string | null;
  agent_versions?: string[];
  profile_versions?: string[];
}

export interface TraceListFilters {
  generation_status?: string;
  delivery_status?: string;
  mode?: string;
  agent_failure?: string;
  rag?: string;
  repair?: string;
  degraded?: string;
  graph_version?: string;
  agent_version?: string;
  profile_version?: string;
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

export interface TraceWorkflowSummary {
  mode: string;
  graph_version: string;
  status: string;
  model_call_count: number;
  tool_call_count: number;
  total_token_count: number;
  repair_count: number;
  repair_budget?: number | null;
  max_repair_attempts?: number | null;
  review_count?: number | null;
  reviewer_rejection_rate?: number | null;
  reviewer_repair_rate?: number | null;
  reviewer_degradation_rate?: number | null;
  degraded: boolean;
}

export type TraceReviewerVerdictStatus = "pass" | "repair" | "reject" | string;

export type TraceReviewerRepairTarget =
  | "orchestrator"
  | "nutrition_expert"
  | "response_style"
  | string;

export interface TraceReviewerIssue {
  type?: string | null;
  excerpt?: string | null;
  explanation?: string | null;
  excerpt_present?: boolean;
  explanation_present?: boolean;
}

export interface TraceReviewerVerdictPayload {
  schema_version?: string | null;
  verdict?: TraceReviewerVerdictStatus | null;
  repair_target?: TraceReviewerRepairTarget | null;
  issue_type?: string | null;
  issue_types?: string[];
  reason_summary?: string | null;
  reason_summary_present?: boolean;
  issues?: TraceReviewerIssue[];
  issue_count?: number | null;
  reviewed_artifact_ids?: string[];
  original_artifact_id?: string | null;
  repaired_artifact_id?: string | null;
  final_artifact_id?: string | null;
  repair_attempt?: number | null;
  repair_budget?: number | Record<string, number> | null;
  artifact_id?: string | null;
  invocation_id?: string | null;
  parent_artifact_ids?: string[];
  attempt?: number | null;
  created_at?: string | null;
  integrity_status?: string | null;
}

export interface TraceReviewerRepairAttempt {
  attempt?: number | null;
  target?: TraceReviewerRepairTarget | null;
  verdict_artifact_id?: string | null;
  input_artifact_id?: string | null;
  output_artifact_id?: string | null;
  status?: string | null;
  transition?: TraceWorkflowTransition | null;
}

export interface TraceReviewerArtifactRef {
  artifact_id?: string | null;
  artifact_type?: string | null;
  producer_role?: string | null;
  schema_version?: string | null;
  integrity_status?: string | null;
  created_at?: string | null;
}

export interface TraceReviewerSummary {
  status?: string | null;
  verdict?: TraceReviewerVerdictStatus | null;
  latest_verdict?: TraceReviewerVerdictPayload | null;
  verdicts?: TraceReviewerVerdictPayload[];
  attempts?: TraceReviewerVerdictPayload[];
  reviewer_invocation_count?: number | null;
  verdict_count?: number | null;
  issue_count?: number | null;
  repair_attempts?: TraceReviewerRepairAttempt[];
  repair_counts?: Record<string, number> | null;
  budget?: {
    exhausted?: boolean;
    exhausted_targets?: string[];
    repair_attempts_observed?: number | null;
    configured_limits?: Record<string, number> | null;
  } | null;
  comparison?: {
    original?: TraceReviewerArtifactRef | null;
    repaired?: TraceReviewerArtifactRef[];
    final_adopted?: TraceReviewerArtifactRef | null;
    changed?: boolean | null;
  } | null;
  rejected?: boolean;
  degraded?: boolean;
  review_count?: number | null;
  rejection_count?: number | null;
  repair_count?: number | null;
  degradation_count?: number | null;
  rejection_rate?: number | null;
  repair_rate?: number | null;
  degradation_rate?: number | null;
  repair_budget?: number | null;
  max_repair_attempts?: number | null;
  remaining_repair_attempts?: number | null;
  original_artifact_id?: string | null;
  repaired_artifact_id?: string | null;
  final_artifact_id?: string | null;
}

export interface TraceWorkflowReviewMetrics {
  window?: Record<string, unknown> | null;
  counts?: Record<string, number> | null;
  rates?: Record<string, number> | null;
  denominators?: Record<string, number> | null;
  by_repair_target?: Record<string, number> | null;
  latency_ms?: { sample_count?: number | null; p50?: number | null; p95?: number | null } | null;
  tokens?: { workflow_count?: number | null; total?: number | null; p50?: number | null; p95?: number | null } | null;
  node_failure_rates?: Record<string, {
    failed?: number | null;
    total?: number | null;
    rate?: number | null;
  }> | null;
  outcomes_by_mode?: Record<string, {
    total?: number | null;
    succeeded?: number | null;
    degraded?: number | null;
    failed?: number | null;
  }> | null;
  citations?: {
    coverage_rate?: number | null;
    invalid_rate?: number | null;
    covered_claim_count?: number | null;
    knowledge_claim_count?: number | null;
    invalid_citation_count?: number | null;
    citation_count?: number | null;
  } | null;
}

export interface StyleABHumanReview {
  review_id: string;
  case_id: string;
  supersedes_review_id: string | null;
  actor: string;
  style_match: number;
  fidelity: number;
  appropriateness: number;
  decision: "accept" | "reject";
  comment: string;
  created_at: string;
}

export interface StyleABSide {
  profile_version: string;
  generation_model: string;
  example_ids: string[];
  response_sha256: string;
  response?: {
    text?: string;
    style_profile_version?: string;
    [key: string]: unknown;
  };
}

export interface StyleABCaseSummary {
  case_id: string;
  case_key: string;
  source_kind: "synthetic_evaluation";
  source_sample_sha256: string;
  response_plan_sha256: string;
  communication_act: string;
  required_communication_acts: string[];
  baseline: StyleABSide;
  candidate: StyleABSide;
  candidate_bundle_sha256: string;
  automated_judge: {
    status: "not_run" | "passed" | "failed" | "error";
    model: string;
    evaluation_sha256: string;
  };
  latest_human_review: StyleABHumanReview | null;
  created_at: string;
}

export interface StyleABCaseDetail extends StyleABCaseSummary {
  response_plan: {
    communication_act?: string;
    content_blocks?: Array<{ block_id?: string; kind?: string; text?: string }>;
    [key: string]: unknown;
  };
  reviews: StyleABHumanReview[];
}

export interface StyleABStatistics {
  counts: {
    case_count: number;
    reviewed_case_count: number;
    pending_case_count: number;
    accepted_case_count: number;
    rejected_case_count: number;
  };
  rates: { acceptance_rate: number };
  denominators: { acceptance_rate: number; score_averages: number };
  scores: Record<string, { sample_count: number; average: number | null }>;
}

export interface StyleABReviewInput {
  style_match: number;
  fidelity: number;
  appropriateness: number;
  decision: "accept" | "reject";
  comment: string;
  corrects_review_id: string | null;
}

export interface TraceAgentInvocation {
  invocation_id: string;
  agent_role: AgentRole;
  agent_version: string;
  attempt: number;
  parent_invocation_id: string | null;
  input_artifact_ids: string[];
  output_artifact_id: string | null;
  status: string;
  model_call_count: number;
  tool_call_count: number;
  total_token_count: number;
  failure_code: string | null;
  failure_reason: string | null;
  reason_summary: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface TraceAgentArtifact extends ArtifactCreatedDetails {
  invocation_id: string | null;
  payload: Record<string, unknown> | null;
  style_profile_version?: string | null;
  body_redacted?: boolean;
  integrity_status: string;
  created_at: string | null;
}

export interface TraceResponsePlanBlock {
  block_id: string;
  kind: string;
  source_refs: string[];
  required: boolean;
}

export interface TraceResponsePlanPayload {
  schema_version?: string;
  communication_act?: string;
  requested_detail?: string;
  content_blocks?: TraceResponsePlanBlock[];
  citation_refs?: string[];
  prohibited_transformations?: string[];
}

export interface TraceStyledResponsePayload {
  schema_version?: string;
  text?: string;
  used_block_ids?: string[];
  used_claim_ids?: string[];
  used_action_ids?: string[];
  preserved_risk_flags?: string[];
  preserved_citation_refs?: string[];
  style_profile_version?: string;
}

export interface TraceResolvedStyleProfilePayload {
  profile_id?: string;
  style_profile_id?: string;
  profile_name?: string;
  display_name?: string;
  name?: string;
  version?: string;
  style_profile_version?: string;
  requested_style_profile_version?: string;
  style_selection_source?: string;
  style_fallback_reason?: string | null;
  communication_act?: string;
  example_ids?: string[];
}

export interface StyleBypassDetails {
  reason_code: string;
  bypass_type?: string;
  artifact_id?: string | null;
}

export interface TraceEvidenceItem {
  evidence_id: string;
  source_type: string;
  authority: "authoritative" | "user_reported" | "observation" | string;
  occurred_at?: string | null;
  content?: Record<string, unknown> | null;
  confidence?: "high" | "medium" | "low" | string | null;
  uncertainty?: string | null;
  source_ref?: string | null;
}

export interface TraceEvidencePacketPayload {
  schema_version?: string;
  turn_id?: string;
  user_request?: string;
  professional_question?: string;
  items?: TraceEvidenceItem[];
  missing_information?: string[];
}

export interface TraceNutritionCalculation {
  observation_id: string;
  calculation_type: string;
  value: number | string;
  unit?: string | null;
  inputs?: Record<string, unknown> | null;
}

export interface TraceKnowledgeCitation {
  rank?: number | null;
  citation_id?: string | null;
  source_id?: string | null;
  chunk_id?: string | null;
  title?: string | null;
  publisher?: string | null;
  published_at?: string | null;
  version?: string | null;
  section_or_page?: string | null;
  source_url?: string | null;
  applicability?: string[];
  review_status?: "draft" | "approved" | "retired" | string | null;
  retrieved_in_invocation_id?: string | null;
  keyword_score?: number | null;
  vector_score?: number | null;
  rerank_score?: number | null;
  match_reasons?: string[];
  adoption_status?: string | null;
  excerpt?: string | null;
  snippet?: string | null;
  content?: string | null;
}

export interface TraceNutritionKnowledgeStatus {
  corpus_status: "empty" | "available" | "unavailable" | "error" | string;
  citations?: TraceKnowledgeCitation[];
  candidates?: TraceKnowledgeCitation[];
  candidate_citations?: TraceKnowledgeCitation[];
  retrieved_candidates?: TraceKnowledgeCitation[];
  adopted_citations?: TraceKnowledgeCitation[];
  final_citations?: TraceKnowledgeCitation[];
  query_summary?: string | null;
}

export interface TraceNutritionObservationsPayload {
  evidence?: TraceEvidenceItem[];
  calculations?: TraceNutritionCalculation[];
  calculation_observations?: TraceNutritionCalculation[];
  knowledge?: TraceNutritionKnowledgeStatus | null;
}

export interface TraceProfessionalClaim {
  claim_id: string;
  category: string;
  statement?: string | null;
  basis_types?: string[];
  evidence_refs?: string[];
  knowledge_refs?: string[];
  confidence?: string;
}

export interface TraceProfessionalAction {
  action_id: string;
  statement?: string | null;
  basis_claim_ids?: string[];
}

export interface TraceProfessionalAssessmentPayload {
  schema_version?: string;
  assessment_type?: string;
  overall?: string;
  findings?: TraceProfessionalClaim[];
  priority_problem?: string | null;
  actions?: TraceProfessionalAction[];
  questions?: string[];
  risk_flags?: string[];
  uncertainty_note?: string | null;
  citations?: TraceKnowledgeCitation[];
}

export type TraceWorkflowTransition = WorkflowTransitionDetails;

export interface TraceComparedResponse {
  artifact_id: string | null;
  content: string | null;
  status: string | null;
}

export interface TraceShadowComparison {
  mode: string;
  delivery_status: string;
  business_writes: string;
  legacy: TraceComparedResponse;
  candidate: TraceComparedResponse;
}

export interface TraceWorkflow {
  summary: TraceWorkflowSummary;
  invocations: TraceAgentInvocation[];
  artifacts: TraceAgentArtifact[];
  transitions: TraceWorkflowTransition[];
  shadow_comparison: TraceShadowComparison | null;
  review?: TraceReviewerSummary | null;
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
  workflow?: TraceWorkflow | null;
  invocations?: TraceAgentInvocation[];
  artifacts?: TraceAgentArtifact[];
  transitions?: TraceWorkflowTransition[];
  shadow_comparison?: TraceShadowComparison | null;
  review?: TraceReviewerSummary | null;
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
