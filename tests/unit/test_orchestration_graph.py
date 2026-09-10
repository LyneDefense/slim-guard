from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.agents.contracts import AgentInvocation
from slim_guard.orchestration.graph import (
    ALLOWED_EDGES,
    GraphLoopBudget,
    GraphLoopCounters,
    GraphNode,
    GraphTransition,
    InvalidGraphTransition,
    InvocationAuthorizationError,
    InvocationGrant,
    LoopBudgetExceeded,
    RepairTarget,
    TransitionReason,
    is_transition_allowed,
    validate_invocation_grant,
    validate_transition,
)


def invocation(*, tools: tuple[str, ...] = ()) -> AgentInvocation:
    return AgentInvocation(
        invocation_id="invocation-1",
        trace_id="trace-1",
        turn_id="turn-1",
        graph_version="typed-supervisor-v1",
        agent_role="nutrition_expert",
        agent_version="nutrition-v1",
        allowed_tools=tools,
        privacy_scopes=("health_summary",),
        deadline_at=datetime.now(UTC) + timedelta(seconds=20),
        max_model_calls=2,
        max_tool_calls=2,
        max_total_tokens=8000,
    )


def test_every_declared_edge_is_accepted_and_unknown_edges_are_rejected() -> None:
    assert ALLOWED_EDGES
    assert all(is_transition_allowed(source, target) for source, target in ALLOWED_EDGES)

    with pytest.raises(InvalidGraphTransition):
        validate_transition(GraphNode.INITIALIZED, GraphNode.DELIVERY)


def test_transition_reason_must_be_valid_for_the_edge() -> None:
    transition = GraphTransition(
        source="review_running",
        target="style_running",
        reason="review_repair",
    )
    assert transition.reason is TransitionReason.REVIEW_REPAIR

    with pytest.raises(ValidationError, match="Illegal workflow transition"):
        GraphTransition(
            source="review_running",
            target="style_running",
            reason="review_passed",
        )


def test_style_can_finish_without_reviewer_or_fall_back_deterministically() -> None:
    assert is_transition_allowed(
        GraphNode.STYLE_RUNNING,
        GraphNode.OUTPUT_GUARDED,
        TransitionReason.RENDERED,
    )


def test_dish_guidance_graph_cannot_skip_retrieval() -> None:
    assert is_transition_allowed(
        GraphNode.ORCHESTRATOR_RUNNING,
        GraphNode.DISH_RECOGNITION_RUNNING,
        TransitionReason.DISH_RECOGNITION,
    )
    assert is_transition_allowed(
        GraphNode.DISH_RECOGNITION_RUNNING,
        GraphNode.NUTRITION_RETRIEVAL_RUNNING,
        TransitionReason.NUTRITION_RETRIEVAL,
    )
    assert is_transition_allowed(
        GraphNode.NUTRITION_EVIDENCE_READY,
        GraphNode.EXPERT_RUNNING,
        TransitionReason.EVIDENCE_BUILT,
    )
    assert not is_transition_allowed(
        GraphNode.DISH_RECOGNITION_RUNNING,
        GraphNode.EXPERT_RUNNING,
    )
    assert is_transition_allowed(
        GraphNode.STYLE_RUNNING,
        GraphNode.NEUTRAL_FALLBACK,
        TransitionReason.STYLE_FAILED,
    )
    assert is_transition_allowed(
        GraphNode.RESPONSE_RENDERING,
        GraphNode.OUTPUT_GUARDED,
        TransitionReason.STYLE_BYPASSED,
    )


def test_invocation_tools_and_scopes_cannot_exceed_trusted_grant() -> None:
    grant = InvocationGrant(
        agent_role="nutrition_expert",
        allowed_tools=frozenset({"calculate_bmi"}),
        privacy_scopes=frozenset({"health_summary"}),
        max_model_calls=2,
        max_tool_calls=2,
        max_total_tokens=8000,
    )
    validate_invocation_grant(invocation(tools=("calculate_bmi",)), grant)

    with pytest.raises(InvocationAuthorizationError, match="unauthorized tools"):
        validate_invocation_grant(invocation(tools=("record_weight",)), grant)


def test_repair_and_context_edges_have_hard_loop_budgets() -> None:
    budget = GraphLoopBudget()
    counters = GraphLoopCounters()
    counters = counters.consume_repair(RepairTarget.RESPONSE_STYLE, budget)
    assert counters.style_repairs == 1

    with pytest.raises(LoopBudgetExceeded, match="repair budget"):
        counters.consume_repair(RepairTarget.RESPONSE_STYLE, budget)

    counters = GraphLoopCounters().consume_context_supplement(
        role="nutrition_expert",  # type: ignore[arg-type]
        budget=budget,
    )
    with pytest.raises(LoopBudgetExceeded, match="context supplement budget"):
        counters.consume_context_supplement(
            role="nutrition_expert",  # type: ignore[arg-type]
            budget=budget,
        )
