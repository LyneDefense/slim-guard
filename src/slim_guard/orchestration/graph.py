"""Finite workflow graph, authorization, and loop budgets.

Semantic routing decisions can come from a model, but every transition and grant is
validated here before the coordinator executes it.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from slim_guard.agents.contracts import AgentInvocation, AgentRole, RepairTarget


class GraphNode(StrEnum):
    INITIALIZED = "initialized"
    INPUT_GUARDED = "input_guarded"
    SAFETY_RENDERED = "safety_rendered"
    MEMORY_INGESTED = "memory_ingested"
    MEMORY_RECALL = "memory_recall"
    CONTEXT_READY = "context_ready"
    ORCHESTRATOR_RUNNING = "orchestrator_running"
    ORCHESTRATOR = "orchestrator_running"
    BUSINESS_TOOL_RUNNING = "business_tool_running"
    BUSINESS_TOOL = "business_tool_running"
    RESPONSE_RENDERING = "response_rendering"
    EVIDENCE_READY = "evidence_ready"
    EXPERT_RUNNING = "expert_running"
    NUTRITION = "expert_running"
    NUTRITION_EXPERT = "expert_running"
    EXPERT_TOOL_RUNNING = "expert_tool_running"
    NUTRITION_TOOL = "expert_tool_running"
    STYLE_RESOLVED = "style_resolved"
    STYLE_RUNNING = "style_running"
    STYLE = "style_running"
    RESPONSE_STYLE = "style_running"
    STYLE_TOOL_RUNNING = "style_tool_running"
    STYLE_TOOL = "style_tool_running"
    REVIEW_RUNNING = "review_running"
    REVIEW = "review_running"
    RESPONSE_REVIEWER = "review_running"
    NEUTRAL_FALLBACK = "neutral_fallback"
    OUTPUT_GUARDED = "output_guarded"
    OUTPUT_GUARD = "output_guarded"
    COMPLETED = "completed"
    DELIVERY = "delivery"


class TransitionReason(StrEnum):
    START = "start"
    INPUT_ALLOWED = "input_allowed"
    INPUT_BLOCKED = "input_blocked"
    SAFETY_RESPONSE_READY = "safety_response_ready"
    MEMORY_INGESTED = "memory_ingested"
    MEMORY_RECALLED = "memory_recalled"
    CONTEXT_READY = "context_ready"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    NEEDS_USER_INPUT = "needs_user_input"
    DIRECT = "direct"
    PROFESSIONAL_ASSESSMENT = "professional_assessment"
    EVIDENCE_BUILT = "evidence_built"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ASSESSMENT_READY = "assessment_ready"
    STYLE_RESOLVED = "style_resolved"
    STYLE_BYPASSED = "style_bypassed"
    STYLE_FAILED = "style_failed"
    RENDERED = "rendered"
    REVIEW_PASSED = "review_passed"
    REVIEW_REPAIR = "review_repair"
    REVIEW_REJECTED = "review_rejected"
    CONTEXT_REQUESTED = "context_requested"
    CONTEXT_SUPPLIED = "context_supplied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FALLBACK_READY = "fallback_ready"
    OUTPUT_REPLACED = "output_replaced"
    OUTPUT_PASSED = "output_passed"
    DELIVERY_STARTED = "delivery_started"


_TRANSITION_REASONS: dict[tuple[GraphNode, GraphNode], frozenset[TransitionReason]] = {
    (GraphNode.INITIALIZED, GraphNode.INPUT_GUARDED): frozenset({TransitionReason.START}),
    (GraphNode.INPUT_GUARDED, GraphNode.SAFETY_RENDERED): frozenset(
        {TransitionReason.INPUT_BLOCKED}
    ),
    (GraphNode.INPUT_GUARDED, GraphNode.MEMORY_INGESTED): frozenset(
        {TransitionReason.INPUT_ALLOWED}
    ),
    (GraphNode.SAFETY_RENDERED, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.SAFETY_RESPONSE_READY}
    ),
    # MEMORY_RECALL is explicit for observability, while the direct edge keeps
    # resume compatibility with the state machine frozen in architecture v1.2.
    (GraphNode.MEMORY_INGESTED, GraphNode.MEMORY_RECALL): frozenset(
        {TransitionReason.MEMORY_INGESTED}
    ),
    (GraphNode.MEMORY_INGESTED, GraphNode.CONTEXT_READY): frozenset(
        {TransitionReason.MEMORY_RECALLED}
    ),
    (GraphNode.MEMORY_RECALL, GraphNode.CONTEXT_READY): frozenset(
        {TransitionReason.MEMORY_RECALLED}
    ),
    (GraphNode.CONTEXT_READY, GraphNode.ORCHESTRATOR_RUNNING): frozenset(
        {TransitionReason.CONTEXT_READY}
    ),
    (GraphNode.ORCHESTRATOR_RUNNING, GraphNode.BUSINESS_TOOL_RUNNING): frozenset(
        {TransitionReason.TOOL_CALL}
    ),
    (GraphNode.BUSINESS_TOOL_RUNNING, GraphNode.ORCHESTRATOR_RUNNING): frozenset(
        {TransitionReason.TOOL_RESULT}
    ),
    (GraphNode.ORCHESTRATOR_RUNNING, GraphNode.RESPONSE_RENDERING): frozenset(
        {TransitionReason.DIRECT, TransitionReason.NEEDS_USER_INPUT}
    ),
    (GraphNode.ORCHESTRATOR_RUNNING, GraphNode.EVIDENCE_READY): frozenset(
        {TransitionReason.PROFESSIONAL_ASSESSMENT}
    ),
    (GraphNode.EVIDENCE_READY, GraphNode.EXPERT_RUNNING): frozenset(
        {TransitionReason.EVIDENCE_BUILT}
    ),
    (GraphNode.EXPERT_RUNNING, GraphNode.EXPERT_TOOL_RUNNING): frozenset(
        {TransitionReason.TOOL_CALL}
    ),
    (GraphNode.EXPERT_TOOL_RUNNING, GraphNode.EXPERT_RUNNING): frozenset(
        {TransitionReason.TOOL_RESULT}
    ),
    (GraphNode.EXPERT_RUNNING, GraphNode.MEMORY_RECALL): frozenset(
        {TransitionReason.CONTEXT_REQUESTED}
    ),
    (GraphNode.MEMORY_RECALL, GraphNode.EXPERT_RUNNING): frozenset(
        {TransitionReason.CONTEXT_SUPPLIED}
    ),
    (GraphNode.EXPERT_RUNNING, GraphNode.RESPONSE_RENDERING): frozenset(
        {
            TransitionReason.INSUFFICIENT_EVIDENCE,
            TransitionReason.ASSESSMENT_READY,
        }
    ),
    (GraphNode.EXPERT_RUNNING, GraphNode.STYLE_RESOLVED): frozenset(
        {TransitionReason.ASSESSMENT_READY}
    ),
    (GraphNode.RESPONSE_RENDERING, GraphNode.STYLE_RESOLVED): frozenset(
        {TransitionReason.STYLE_RESOLVED}
    ),
    (GraphNode.RESPONSE_RENDERING, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.STYLE_BYPASSED}
    ),
    (GraphNode.STYLE_RESOLVED, GraphNode.STYLE_RUNNING): frozenset(
        {TransitionReason.STYLE_RESOLVED}
    ),
    (GraphNode.STYLE_RUNNING, GraphNode.STYLE_TOOL_RUNNING): frozenset(
        {TransitionReason.TOOL_CALL}
    ),
    (GraphNode.STYLE_TOOL_RUNNING, GraphNode.STYLE_RUNNING): frozenset(
        {TransitionReason.TOOL_RESULT}
    ),
    (GraphNode.STYLE_RUNNING, GraphNode.REVIEW_RUNNING): frozenset(
        {TransitionReason.RENDERED}
    ),
    (GraphNode.STYLE_RUNNING, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.RENDERED}
    ),
    (GraphNode.STYLE_RUNNING, GraphNode.NEUTRAL_FALLBACK): frozenset(
        {TransitionReason.STYLE_FAILED, TransitionReason.BUDGET_EXHAUSTED}
    ),
    (GraphNode.REVIEW_RUNNING, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.REVIEW_PASSED}
    ),
    (GraphNode.REVIEW_RUNNING, GraphNode.STYLE_RUNNING): frozenset(
        {TransitionReason.REVIEW_REPAIR}
    ),
    (GraphNode.REVIEW_RUNNING, GraphNode.EXPERT_RUNNING): frozenset(
        {TransitionReason.REVIEW_REPAIR}
    ),
    (GraphNode.REVIEW_RUNNING, GraphNode.ORCHESTRATOR_RUNNING): frozenset(
        {TransitionReason.REVIEW_REPAIR}
    ),
    (GraphNode.REVIEW_RUNNING, GraphNode.NEUTRAL_FALLBACK): frozenset(
        {
            TransitionReason.REVIEW_REJECTED,
            TransitionReason.BUDGET_EXHAUSTED,
        }
    ),
    (GraphNode.NEUTRAL_FALLBACK, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.FALLBACK_READY}
    ),
    (GraphNode.OUTPUT_GUARDED, GraphNode.OUTPUT_GUARDED): frozenset(
        {TransitionReason.OUTPUT_REPLACED}
    ),
    (GraphNode.OUTPUT_GUARDED, GraphNode.COMPLETED): frozenset(
        {TransitionReason.OUTPUT_PASSED}
    ),
    (GraphNode.COMPLETED, GraphNode.DELIVERY): frozenset(
        {TransitionReason.DELIVERY_STARTED}
    ),
}

ALLOWED_TRANSITIONS = MappingProxyType(_TRANSITION_REASONS)
ALLOWED_EDGES = frozenset(_TRANSITION_REASONS)


class WorkflowErrorCategory(StrEnum):
    INVALID_TRANSITION = "invalid_transition"
    SCHEMA_VALIDATION = "schema_validation"
    ARTIFACT_REFERENCE = "artifact_reference"
    ARTIFACT_INTEGRITY = "artifact_integrity"
    INVOCATION_AUTHORIZATION = "invocation_authorization"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    MODEL_FAILURE = "model_failure"
    TOOL_FAILURE = "tool_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REVIEW_REJECTED = "review_rejected"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


# Shorter name used by implementation-plan discussions.
GraphErrorCategory = WorkflowErrorCategory


class WorkflowGraphError(RuntimeError):
    category = WorkflowErrorCategory.INTERNAL


class InvalidGraphTransition(WorkflowGraphError):
    category = WorkflowErrorCategory.INVALID_TRANSITION

    def __init__(
        self,
        source: GraphNode,
        target: GraphNode,
        reason: TransitionReason | None = None,
    ) -> None:
        detail = f"Illegal workflow transition: {source.value} -> {target.value}"
        if reason is not None:
            detail += f" ({reason.value})"
        super().__init__(detail)
        self.source = source
        self.target = target
        self.reason = reason


# Compatibility spelling used by callers that treat graph errors as assertions.
IllegalGraphTransition = InvalidGraphTransition


class InvocationAuthorizationError(WorkflowGraphError):
    category = WorkflowErrorCategory.INVOCATION_AUTHORIZATION


class LoopBudgetExceeded(WorkflowGraphError):
    category = WorkflowErrorCategory.BUDGET_EXHAUSTED


def is_transition_allowed(
    source: GraphNode,
    target: GraphNode,
    reason: TransitionReason | None = None,
) -> bool:
    reasons = ALLOWED_TRANSITIONS.get((source, target))
    return reasons is not None and (reason is None or reason in reasons)


def validate_transition(
    source: GraphNode,
    target: GraphNode,
    reason: TransitionReason | None = None,
) -> None:
    if not is_transition_allowed(source, target, reason):
        raise InvalidGraphTransition(source, target, reason)


assert_transition_allowed = validate_transition


class GraphTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: GraphNode
    target: GraphNode
    reason: TransitionReason
    invocation_id: str | None = Field(default=None, min_length=1, max_length=128)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_edge(self) -> Self:
        if not is_transition_allowed(self.source, self.target, self.reason):
            error = InvalidGraphTransition(self.source, self.target, self.reason)
            raise ValueError(str(error))
        return self


class InvocationGrant(BaseModel):
    """Trusted maximum permissions configured for an agent node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_role: AgentRole
    allowed_tools: frozenset[str] = Field(
        default_factory=frozenset,
        validation_alias=AliasChoices("allowed_tools", "allowed_tool_names"),
        max_length=64,
    )
    privacy_scopes: frozenset[str] = Field(default_factory=frozenset, max_length=64)
    max_model_calls: int = Field(default=6, ge=1, le=32, strict=True)
    max_tool_calls: int = Field(default=8, ge=0, le=64, strict=True)
    max_total_tokens: int = Field(default=64_000, ge=1, le=10_000_000, strict=True)


def validate_invocation_grant(invocation: AgentInvocation, grant: InvocationGrant) -> None:
    """Reject model/tool/scope escalation relative to trusted node configuration."""

    if invocation.agent_role is not grant.agent_role:
        raise InvocationAuthorizationError(
            f"Invocation role {invocation.agent_role.value} does not match its grant"
        )
    unauthorized_tools = set(invocation.allowed_tools).difference(grant.allowed_tools)
    if unauthorized_tools:
        raise InvocationAuthorizationError(
            "Invocation requested unauthorized tools: " + ", ".join(sorted(unauthorized_tools))
        )
    unauthorized_scopes = set(invocation.privacy_scopes).difference(grant.privacy_scopes)
    if unauthorized_scopes:
        raise InvocationAuthorizationError(
            "Invocation requested unauthorized privacy scopes: "
            + ", ".join(sorted(unauthorized_scopes))
        )
    if invocation.max_model_calls > grant.max_model_calls:
        raise InvocationAuthorizationError("Invocation exceeds its model-call grant")
    if invocation.max_tool_calls > grant.max_tool_calls:
        raise InvocationAuthorizationError("Invocation exceeds its tool-call grant")
    if invocation.max_total_tokens > grant.max_total_tokens:
        raise InvocationAuthorizationError("Invocation exceeds its token grant")


class GraphLoopBudget(BaseModel):
    """Hard limits for every backward edge in one turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_context_supplements_per_specialist: int = Field(default=1, ge=0, le=4, strict=True)
    max_style_repairs: int = Field(default=1, ge=0, le=4, strict=True)
    max_nutrition_repairs: int = Field(default=1, ge=0, le=4, strict=True)
    max_orchestrator_repairs: int = Field(default=1, ge=0, le=4, strict=True)
    max_upstream_repairs: int = Field(default=2, ge=0, le=8, strict=True)


# Concise public name.
LoopBudget = GraphLoopBudget


class SpecialistContextCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    count: int = Field(default=0, ge=0, le=4, strict=True)


class GraphLoopCounters(BaseModel):
    """Immutable counters returned after consuming a loop edge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    style_repairs: int = Field(default=0, ge=0, le=4, strict=True)
    nutrition_repairs: int = Field(default=0, ge=0, le=4, strict=True)
    orchestrator_repairs: int = Field(default=0, ge=0, le=4, strict=True)
    upstream_repairs: int = Field(default=0, ge=0, le=8, strict=True)
    context_supplements: tuple[SpecialistContextCount, ...] = ()

    @model_validator(mode="after")
    def validate_unique_context_roles(self) -> Self:
        roles = tuple(item.role for item in self.context_supplements)
        if len(roles) != len(set(roles)):
            raise ValueError("Context supplement counters must have unique roles")
        return self

    def consume_repair(
        self,
        target: RepairTarget,
        budget: GraphLoopBudget,
    ) -> GraphLoopCounters:
        values = self.model_dump()
        values["upstream_repairs"] = self.upstream_repairs + 1
        if values["upstream_repairs"] > budget.max_upstream_repairs:
            raise LoopBudgetExceeded("The turn-wide upstream repair budget is exhausted")

        field_name: str
        maximum: int
        if target is RepairTarget.RESPONSE_STYLE:
            field_name = "style_repairs"
            maximum = budget.max_style_repairs
        elif target is RepairTarget.NUTRITION_EXPERT:
            field_name = "nutrition_repairs"
            maximum = budget.max_nutrition_repairs
        else:
            field_name = "orchestrator_repairs"
            maximum = budget.max_orchestrator_repairs
        values[field_name] = int(values[field_name]) + 1
        if values[field_name] > maximum:
            raise LoopBudgetExceeded(f"The {target.value} repair budget is exhausted")
        return GraphLoopCounters.model_validate(values)

    def consume_context_supplement(
        self,
        role: AgentRole | str,
        budget: GraphLoopBudget,
    ) -> GraphLoopCounters:
        role = AgentRole(role)
        if role is AgentRole.ORCHESTRATOR:
            raise LoopBudgetExceeded("Orchestrator context is not a specialist supplement")
        counts = {item.role: item.count for item in self.context_supplements}
        next_count = counts.get(role, 0) + 1
        if next_count > budget.max_context_supplements_per_specialist:
            raise LoopBudgetExceeded(f"The {role.value} context supplement budget is exhausted")
        counts[role] = next_count
        return self.model_copy(
            update={
                "context_supplements": tuple(
                    SpecialistContextCount(role=item_role, count=count)
                    for item_role, count in sorted(counts.items(), key=lambda item: item[0].value)
                )
            }
        )


LoopCounters = GraphLoopCounters


def classify_workflow_error(error: BaseException) -> WorkflowErrorCategory:
    if isinstance(error, WorkflowGraphError):
        return error.category
    return WorkflowErrorCategory.INTERNAL


__all__ = [
    "ALLOWED_EDGES",
    "ALLOWED_TRANSITIONS",
    "GraphErrorCategory",
    "GraphLoopBudget",
    "GraphLoopCounters",
    "GraphNode",
    "GraphTransition",
    "IllegalGraphTransition",
    "InvalidGraphTransition",
    "InvocationAuthorizationError",
    "InvocationGrant",
    "LoopBudget",
    "LoopBudgetExceeded",
    "LoopCounters",
    "SpecialistContextCount",
    "TransitionReason",
    "WorkflowErrorCategory",
    "WorkflowGraphError",
    "assert_transition_allowed",
    "classify_workflow_error",
    "is_transition_allowed",
    "validate_invocation_grant",
    "validate_transition",
]
