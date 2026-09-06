import type {
  AgentRole,
  TimelineEvent,
  TraceAgentArtifact,
  TraceAgentInvocation,
  TraceDetail,
  TraceShadowComparison,
  TraceWorkflowTransition,
} from "../../types";

const MULTI_AGENT_OPERATIONS = new Set([
  "invocation_started",
  "invocation_result",
  "artifact_created",
  "workflow_transition",
  "response_adopted",
  "response_degraded",
]);

const AGENT_ROLES = new Set<AgentRole>([
  "orchestrator",
  "nutrition_expert",
  "response_style",
  "response_reviewer",
]);

export interface AgentInvocationView extends TraceAgentInvocation {
  duration_ms: number | null;
  repaired: boolean;
  events: TimelineEvent[];
}

export interface ShadowResponseView {
  artifact_id: string | null;
  content: string | null;
  status: string | null;
}

export interface ShadowComparisonView {
  mode: string;
  delivery_status: string;
  business_writes: string;
  legacy: ShadowResponseView;
  candidate: ShadowResponseView;
}

export interface WorkflowTraceView {
  mode: string;
  graph_version: string | null;
  status: string;
  invocations: AgentInvocationView[];
  artifacts: TraceAgentArtifact[];
  transitions: TraceWorkflowTransition[];
  shadowComparison: ShadowComparisonView | null;
  timeline: TimelineEvent[];
  turnEvents: TimelineEvent[];
  hasMultiAgentTrace: boolean;
}

export const AGENT_ROLE_LABELS: Record<AgentRole, string> = {
  orchestrator: "对话编排 Agent",
  nutrition_expert: "营养专业 Agent",
  response_style: "表达风格 Agent",
  response_reviewer: "忠实度审查 Agent",
};

export function agentRoleLabel(role: string): string {
  return isAgentRole(role) ? AGENT_ROLE_LABELS[role] : role;
}

export function buildWorkflowTrace(data: TraceDetail): WorkflowTraceView {
  const eventInvocations = invocationsFromEvents(data.timeline);
  const apiInvocations = data.workflow?.invocations ?? data.invocations ?? [];
  const rawInvocations = apiInvocations.length > 0 ? apiInvocations : eventInvocations;
  const artifacts = data.workflow?.artifacts ?? data.artifacts ?? artifactsFromEvents(data.timeline);
  const transitions =
    data.workflow?.transitions ?? data.transitions ?? transitionsFromEvents(data.timeline);
  const grouped = groupEventsByInvocation(data.timeline, rawInvocations, artifacts);
  const invocations = rawInvocations
    .map((invocation) => ({
      ...invocation,
      duration_ms:
        invocation.duration_ms ?? durationBetween(invocation.started_at, invocation.completed_at),
      repaired: isRepairInvocation(invocation, transitions),
      events: grouped.byInvocation.get(invocation.invocation_id) ?? [],
    }))
    .sort(compareInvocations);
  const summary = data.workflow?.summary;
  const adopted = latestEventDetails(data.timeline, "response_adopted");
  const mode =
    summary?.mode ?? stringValue(adopted?.mode) ?? (invocations.length > 0 ? "unknown" : "legacy");
  const apiComparison = data.workflow?.shadow_comparison ?? data.shadow_comparison ?? null;

  return {
    mode,
    graph_version: summary?.graph_version ?? null,
    status: summary?.status ?? inferWorkflowStatus(invocations),
    invocations,
    artifacts,
    transitions,
    shadowComparison: buildShadowComparison(data, apiComparison, artifacts, mode, adopted),
    timeline: data.timeline,
    turnEvents: grouped.turnEvents,
    hasMultiAgentTrace:
      invocations.length > 0 ||
      transitions.length > 0 ||
      data.timeline.some((event) => MULTI_AGENT_OPERATIONS.has(event.operation)),
  };
}

function invocationsFromEvents(timeline: TimelineEvent[]): TraceAgentInvocation[] {
  const invocations = new Map<string, TraceAgentInvocation>();
  for (const event of timeline) {
    const details = asRecord(event.details);
    if (event.operation === "invocation_started") {
      const invocationId = stringValue(details.invocation_id);
      const role = stringValue(details.agent_role);
      if (!invocationId || !role || !isAgentRole(role)) continue;
      invocations.set(invocationId, {
        invocation_id: invocationId,
        agent_role: role,
        agent_version: stringValue(details.agent_version) ?? "unknown",
        attempt: numberValue(details.attempt) ?? 1,
        parent_invocation_id: nullableString(details.parent_invocation_id),
        input_artifact_ids: stringArray(details.input_artifact_ids),
        output_artifact_id: null,
        status: event.status === "failed" ? "failed" : "running",
        model_call_count: 0,
        tool_call_count: 0,
        total_token_count: 0,
        failure_code: null,
        failure_reason: null,
        reason_summary: stringValue(details.reason_summary),
        started_at: stringValue(details.started_at) ?? event.started_at,
        completed_at: null,
        duration_ms: null,
      });
      continue;
    }
    if (event.operation !== "invocation_result") continue;
    const invocationId = stringValue(details.invocation_id);
    const current = invocationId ? invocations.get(invocationId) : undefined;
    if (!invocationId || !current) continue;
    const completedAt = stringValue(details.completed_at) ?? event.completed_at ?? event.started_at;
    invocations.set(invocationId, {
      ...current,
      output_artifact_id: nullableString(details.output_artifact_id),
      status: stringValue(details.status) ?? event.status,
      model_call_count: numberValue(details.model_call_count) ?? 0,
      tool_call_count: numberValue(details.tool_call_count) ?? 0,
      total_token_count: numberValue(details.total_token_count) ?? 0,
      failure_code: nullableString(details.failure_code),
      completed_at: completedAt,
      duration_ms: durationBetween(current.started_at, completedAt),
    });
  }
  return [...invocations.values()];
}

function artifactsFromEvents(timeline: TimelineEvent[]): TraceAgentArtifact[] {
  return timeline.flatMap((event) => {
    if (event.operation !== "artifact_created") return [];
    const details = asRecord(event.details);
    const artifactId = stringValue(details.artifact_id);
    if (!artifactId) return [];
    return [{
      artifact_id: artifactId,
      artifact_type: stringValue(details.artifact_type) ?? "unknown",
      producer_role: stringValue(details.producer_role) ?? "unknown",
      schema_version: stringValue(details.schema_version) ?? "unknown",
      parent_artifact_ids: stringArray(details.parent_artifact_ids),
      payload_sha256: stringValue(details.payload_sha256) ?? "",
      invocation_id: null,
      payload: null,
      integrity_status: "metadata_only",
      created_at: event.started_at,
    }];
  });
}

function transitionsFromEvents(timeline: TimelineEvent[]): TraceWorkflowTransition[] {
  return timeline.flatMap((event) => {
    if (event.operation !== "workflow_transition") return [];
    const details = asRecord(event.details);
    const fromNode = stringValue(details.from_node);
    const toNode = stringValue(details.to_node);
    if (!fromNode || !toNode) return [];
    return [{
      from_node: fromNode,
      to_node: toNode,
      transition_type: stringValue(details.transition_type) ?? "forward",
      reason_code: stringValue(details.reason_code) ?? "unspecified",
      attempt: numberValue(details.attempt) ?? 1,
    }];
  });
}

function groupEventsByInvocation(
  timeline: TimelineEvent[],
  invocations: TraceAgentInvocation[],
  artifacts: TraceAgentArtifact[],
): { byInvocation: Map<string, TimelineEvent[]>; turnEvents: TimelineEvent[] } {
  const byInvocation = new Map(
    invocations.map((invocation) => [invocation.invocation_id, [] as TimelineEvent[]]),
  );
  const outputOwners = new Map(
    invocations
      .filter((invocation) => invocation.output_artifact_id)
      .map((invocation) => [invocation.output_artifact_id!, invocation.invocation_id]),
  );
  for (const artifact of artifacts) {
    if (artifact.invocation_id) outputOwners.set(artifact.artifact_id, artifact.invocation_id);
  }
  const active: string[] = [];
  const turnEvents: TimelineEvent[] = [];

  for (const event of timeline) {
    const details = asRecord(event.details);
    const directId = stringValue(details.invocation_id);
    if (event.operation === "invocation_started" && directId) active.push(directId);

    let invocationId = directId && byInvocation.has(directId) ? directId : null;
    if (!invocationId && event.operation === "artifact_created") {
      const artifactId = stringValue(details.artifact_id);
      invocationId = artifactId ? outputOwners.get(artifactId) ?? null : null;
      if (!invocationId) {
        invocationId = latestActiveInvocation(
          active,
          invocations,
          stringValue(details.producer_role),
        );
      }
    }
    if (!invocationId && !MULTI_AGENT_OPERATIONS.has(event.operation)) {
      invocationId = active.at(-1) ?? null;
    }

    if (invocationId && byInvocation.has(invocationId)) {
      byInvocation.get(invocationId)!.push(event);
    } else if (!["workflow_transition", "response_adopted", "response_degraded"].includes(event.operation)) {
      turnEvents.push(event);
    }

    if (event.operation === "invocation_result" && directId) {
      const activeIndex = active.lastIndexOf(directId);
      if (activeIndex >= 0) active.splice(activeIndex, 1);
    }
  }
  return { byInvocation, turnEvents };
}

function latestActiveInvocation(
  active: string[],
  invocations: TraceAgentInvocation[],
  role: string | null,
): string | null {
  if (!role) return null;
  for (let index = active.length - 1; index >= 0; index -= 1) {
    const invocation = invocations.find((item) => item.invocation_id === active[index]);
    if (invocation?.agent_role === role) return invocation.invocation_id;
  }
  return null;
}

function buildShadowComparison(
  data: TraceDetail,
  comparison: TraceShadowComparison | null,
  artifacts: TraceAgentArtifact[],
  mode: string,
  adopted: Record<string, unknown> | null,
): ShadowComparisonView | null {
  const comparisonMode = comparison?.mode ?? mode;
  if (comparisonMode !== "shadow") return null;

  const adoptedArtifactId = nullableString(adopted?.artifact_id);
  const adoptedArtifact = artifacts.find((artifact) => artifact.artifact_id === adoptedArtifactId);
  const candidateContent = contentFromPayload(adoptedArtifact?.payload);
  return {
    mode: comparisonMode,
    delivery_status: comparison?.delivery_status ?? "not_sent",
    business_writes: comparison?.business_writes ?? "no_business_writes",
    legacy: comparison?.legacy ?? {
      artifact_id: null,
      content: data.output?.content ?? null,
      status: data.output?.status ?? null,
    },
    candidate: comparison?.candidate ?? {
      artifact_id: adoptedArtifactId,
      content: candidateContent,
      status: candidateContent ? "generated" : "unavailable",
    },
  };
}

function contentFromPayload(payload: unknown): string | null {
  const value = asRecord(payload);
  const direct = stringValue(value.content) ?? stringValue(value.text);
  if (direct) return direct;
  for (const key of ["response", "styled_response", "candidate"]) {
    const nested = asRecord(value[key]);
    const content = stringValue(nested.content) ?? stringValue(nested.text);
    if (content) return content;
  }
  return null;
}

function isRepairInvocation(
  invocation: TraceAgentInvocation,
  transitions: TraceWorkflowTransition[],
): boolean {
  if (invocation.attempt > 1) return true;
  const nodeNames: Record<AgentRole, string[]> = {
    orchestrator: ["orchestrator", "orchestrator_running"],
    nutrition_expert: ["nutrition", "nutrition_running", "nutrition_expert"],
    response_style: ["style", "style_running", "response_style"],
    response_reviewer: ["review", "review_running", "response_reviewer"],
  };
  return transitions.some((transition) => {
    const isTarget = nodeNames[invocation.agent_role].includes(transition.to_node);
    const repairMarker = `${transition.transition_type} ${transition.reason_code}`.toLowerCase();
    return isTarget && (transition.attempt > 1 || /repair|retry|return|drift|changed/.test(repairMarker));
  });
}

function inferWorkflowStatus(invocations: AgentInvocationView[]): string {
  if (invocations.some((invocation) => invocation.status === "failed")) return "failed";
  if (invocations.some((invocation) => invocation.status === "degraded")) return "degraded";
  if (invocations.some((invocation) => ["running", "started"].includes(invocation.status))) return "running";
  return invocations.length > 0 ? "succeeded" : "unknown";
}

function latestEventDetails(
  timeline: TimelineEvent[],
  operation: string,
): Record<string, unknown> | null {
  const event = [...timeline].reverse().find((item) => item.operation === operation);
  return event ? asRecord(event.details) : null;
}

function compareInvocations(left: TraceAgentInvocation, right: TraceAgentInvocation): number {
  return new Date(left.started_at).getTime() - new Date(right.started_at).getTime();
}

function durationBetween(startedAt: string, completedAt: string | null): number | null {
  if (!completedAt) return null;
  const duration = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  return Number.isFinite(duration) && duration >= 0 ? duration : null;
}

function isAgentRole(value: string): value is AgentRole {
  return AGENT_ROLES.has(value as AgentRole);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nullableString(value: unknown): string | null {
  return value == null ? null : stringValue(value);
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
