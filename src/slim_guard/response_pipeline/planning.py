"""Build the content contract that separates Core output from expression."""

from __future__ import annotations

from dataclasses import dataclass

from slim_guard.agents.contracts import (
    CommunicationAct,
    ContentBlockKind,
    ProfessionalAssessment,
    RequestedDetail,
    ResponseContentBlock,
    ResponsePlan,
)
from slim_guard.agents.nutrition import CONSULT_NUTRITION_TOOL_NAME
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
        return PlannedResponse(
            plan=ResponsePlan(
                communication_act=self._communication_act(neutral_draft, tool_outcomes),
                requested_detail=RequestedDetail.NORMAL,
                content_blocks=(
                    ResponseContentBlock(
                        block_id="core-neutral-draft",
                        kind=ContentBlockKind.SOCIAL_ACT,
                        text=neutral_draft,
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
            if execution.tool_name != CONSULT_NUTRITION_TOOL_NAME:
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

    @staticmethod
    def _communication_act(
        neutral_draft: str,
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> CommunicationAct:
        if any(
            outcome.execution.tool_name == CONSULT_NUTRITION_TOOL_NAME
            for outcome in outcomes
        ):
            return CommunicationAct.EXPLAIN
        if any(
            outcome.execution.result.status.value == "succeeded"
            and outcome.execution.tool_name.startswith(
                ("record_", "update_", "delete_", "set_", "cancel_")
            )
            for outcome in outcomes
        ):
            return CommunicationAct.ACKNOWLEDGE
        if neutral_draft.rstrip().endswith(("?", "？")):
            return CommunicationAct.ASK
        return CommunicationAct.ENCOURAGE


__all__ = ["PlannedResponse", "ResponsePlanBuilder"]
