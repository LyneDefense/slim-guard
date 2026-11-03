"""Project verified evidence for dialogue planning without deciding who speaks."""

from typing import Any

from slim_guard.agents.contracts import ProfessionalAssessment
from slim_guard.harness.tool_calls import ToolCallOutcome


def tool_observations(outcomes: tuple[ToolCallOutcome, ...]) -> tuple[dict[str, object], ...]:
    # Internal reads, successes and failures are context, never appearance rules.
    return tuple(
        {
            "tool_name": outcome.execution.tool_name,
            "source_ref": outcome.execution.tool_call_id,
            "mutation_kind": outcome.execution.mutation_kind.value,
            "status": outcome.execution.result.status.value,
            "result": _visible_result(outcome.execution.result.output),
            "failure": (
                outcome.execution.result.failure.model_dump(mode="json")
                if outcome.execution.result.failure is not None
                else None
            ),
        }
        for outcome in outcomes
    )


def assessment_summary(assessment: ProfessionalAssessment) -> dict[str, object]:
    """Keep raw RAG documents/citation objects in their original evidence artifacts."""
    return {
        "assessment_type": assessment.assessment_type.value,
        "overall": assessment.overall,
        "findings": tuple(
            {
                "category": finding.category,
                "statement": finding.statement,
                "confidence": finding.confidence.value,
            }
            for finding in assessment.findings
        ),
        "priority_problem": assessment.priority_problem,
        "actions": tuple(action.statement for action in assessment.actions),
        "questions": assessment.questions,
        "risk_flags": assessment.risk_flags,
        "uncertainty_note": assessment.uncertainty_note,
    }


def _visible_result(value: Any) -> Any:
    """Remove persistence plumbing before observations reach the dialogue model."""
    if isinstance(value, dict):
        return {
            key: _visible_result(item)
            for key, item in value.items()
            if key
            not in {
                "id",
                "record_id",
                "artifact_id",
                "citation_id",
                "assessment",
                "citations",
                "idempotency_key",
                "created_at",
                "updated_at",
                "occurred_at",
                "timestamp",
                "source_ids",
            }
        }
    if isinstance(value, list):
        return [_visible_result(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_visible_result(item) for item in value)
    return value
