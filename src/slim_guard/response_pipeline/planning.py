"""Build the content contract that separates Core output from expression."""

from __future__ import annotations

from dataclasses import dataclass

from slim_guard.agents.contracts import (
    ContentBlockKind,
    ProfessionalAssessment,
    RequestedDetail,
    ResponseContentBlock,
    ResponsePlan,
)
from slim_guard.harness.tool_calls import ToolCallOutcome


@dataclass(frozen=True, slots=True)
class PlannedResponse:
    plan: ResponsePlan
    assessment: ProfessionalAssessment | None
    assessment_artifact_id: str | None


class ResponsePlanBuilder:
    """Convert the Core Agent's one neutral draft into a protected response plan.

    This is deliberately deterministic: it does not perform a second round of task
    understanding and cannot add content that the Core Agent did not produce.
    """

    def build(
        self,
        *,
        neutral_draft: str,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> PlannedResponse:
        assessment, assessment_artifact_id = self._nutrition_assessment(tool_outcomes)
        return self.build_with_assessment(
            neutral_draft=neutral_draft,
            assessment=assessment,
            assessment_artifact_id=assessment_artifact_id,
            tool_outcomes=tool_outcomes,
        )

    def build_with_assessment(
        self,
        *,
        neutral_draft: str,
        assessment: ProfessionalAssessment | None,
        assessment_artifact_id: str | None,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> PlannedResponse:
        """Build from an explicitly selected, immutable professional assessment."""

        citations = (
            tuple(citation.citation_id for citation in assessment.citations)
            if assessment is not None
            else ()
        )
        tool_refs = tuple(
            outcome.execution.tool_call_id
            for outcome in tool_outcomes
            if outcome.execution.result.status.value == "succeeded"
        )
        return PlannedResponse(
            plan=ResponsePlan(
                requested_detail=RequestedDetail.NORMAL,
                content_blocks=(
                    ResponseContentBlock(
                        block_id="core-neutral-draft",
                        kind=ContentBlockKind.SOCIAL_ACT,
                        text=neutral_draft,
                        source_refs=tuple(dict.fromkeys((*citations, *tool_refs))),
                    ),
                ),
                citation_refs=citations,
                prohibited_transformations=(
                    "change_facts_numbers_units_times_or_record_status",
                    "change_uncertainty_or_risk",
                    "add_professional_advice",
                    "add_nutrition_estimate",
                    "add_avoidance",
                ),
            ),
            assessment=assessment,
            assessment_artifact_id=assessment_artifact_id,
        )

    @staticmethod
    def _nutrition_assessment(
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> tuple[ProfessionalAssessment | None, str | None]:
        for outcome in reversed(outcomes):
            execution = outcome.execution
            if execution.result.status.value != "succeeded":
                continue
            payload = execution.result.output
            raw = payload.get("assessment")
            if not isinstance(raw, dict):
                continue
            try:
                assessment = ProfessionalAssessment.model_validate(raw)
            except ValueError:
                continue
            artifact_id = payload.get("artifact_id")
            return assessment, artifact_id if isinstance(artifact_id, str) else None
        return None, None


__all__ = ["PlannedResponse", "ResponsePlanBuilder"]
