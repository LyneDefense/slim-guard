"""Bounded reviewer repair routing to the Agent that owns the problem."""

from __future__ import annotations

from slim_guard.agents.contracts import RepairTarget, ReviewerVerdict, ReviewerVerdictStatus
from slim_guard.agents.nutrition import NutritionRepairRequest, NutritionSpecialist
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.planning import PlannedResponse
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile
from slim_guard.response_pipeline.stages import ResponseStageExecutor
from slim_guard.runtime.contracts import AgentArtifact, InvocationStatus


class OwnerRepairCoordinator:
    """Route one repair and one re-review without giving Reviewer write access."""

    def __init__(
        self,
        *,
        stages: ResponseStageExecutor,
        nutrition_specialist: NutritionSpecialist | None,
    ) -> None:
        self._stages = stages
        self._nutrition_specialist = nutrition_specialist

    async def repair(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        selection: ResolvedStyleProfile,
        original_plan_artifact: AgentArtifact,
        prior_styled_artifact: AgentArtifact,
        resolution_artifact: AgentArtifact,
        verdict: ReviewerVerdict,
        verdict_artifact: AgentArtifact,
        reviewer_invocation_id: str,
        calls: int,
        tokens: int,
    ) -> ResponseFinalizationResult:
        feedback = self._stages.review_feedback(verdict)
        assessment = planned.assessment
        assessment_artifact_id = planned.assessment_artifact_id
        parent_invocation_id = reviewer_invocation_id
        nutrition_repaired = False

        if verdict.repair_target is RepairTarget.NUTRITION_EXPERT:
            if self._nutrition_specialist is None or assessment_artifact_id is None:
                return await self._stages.safe_fallback(
                    request=request,
                    selection=selection,
                    plan_artifact=original_plan_artifact,
                    verdict_artifact=verdict_artifact,
                    calls=calls,
                    tokens=tokens,
                    failure_code="nutrition_repair_boundary_unavailable",
                )
            try:
                nutrition = await self._nutrition_specialist.repair(
                    NutritionRepairRequest(
                        trace_id=request.trace_id,
                        thread_id=request.thread_id,
                        turn_id=request.turn_id,
                        parent_invocation_id=reviewer_invocation_id,
                        assessment_artifact_id=assessment_artifact_id,
                        verdict_artifact_id=verdict_artifact.artifact_id,
                        review_feedback=feedback,
                        deadline_at=request.deadline_at,
                    )
                )
            except (RuntimeError, ValueError):
                return await self._stages.safe_fallback(
                    request=request,
                    selection=selection,
                    plan_artifact=original_plan_artifact,
                    verdict_artifact=verdict_artifact,
                    calls=calls,
                    tokens=tokens,
                    failure_code="nutrition_repair_failed",
                )
            calls += nutrition.model_call_count
            tokens += nutrition.total_token_count
            assessment = nutrition.assessment
            assessment_artifact_id = nutrition.artifact.artifact_id
            parent_invocation_id = nutrition.invocation.invocation_id
            nutrition_repaired = True

        core_stage = await self._stages.run_core_repair(
            request=request,
            planned=planned,
            original_plan_artifact=original_plan_artifact,
            assessment=assessment,
            assessment_artifact_id=assessment_artifact_id,
            verdict=verdict,
            verdict_artifact=verdict_artifact,
            parent_invocation_id=parent_invocation_id,
        )
        if core_stage is None:
            return await self._stages.safe_fallback(
                request=request,
                selection=selection,
                plan_artifact=original_plan_artifact,
                verdict_artifact=verdict_artifact,
                calls=calls,
                tokens=tokens,
                failure_code="core_repair_failed",
                nutrition_repaired=nutrition_repaired,
            )
        calls += core_stage.model_call_count
        tokens += core_stage.total_token_count
        style = await self._stages.run_style(
            request=request,
            planned=core_stage.planned,
            selection=selection,
            plan_artifact=core_stage.plan_artifact,
            neutral_artifact=core_stage.neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=core_stage.invocation_id,
            attempt=2,
            prior_artifact_id=prior_styled_artifact.artifact_id,
            verdict_artifact_id=verdict_artifact.artifact_id,
        )
        calls += style.model_call_count
        tokens += style.total_token_count
        review = await self._stages.run_review(
            request=request,
            planned=core_stage.planned,
            selection=selection,
            styled=style.response,
            styled_artifact=style.artifact,
            plan_artifact=core_stage.plan_artifact,
            parent_invocation_id=style.invocation_id,
            attempt=2,
            prior_verdict_artifact_id=verdict_artifact.artifact_id,
        )
        calls += review.model_call_count
        tokens += review.total_token_count
        if review.status is InvocationStatus.SUCCEEDED and (
            review.verdict.verdict is ReviewerVerdictStatus.PASS
        ):
            return ResponseFinalizationResult(
                text=style.response.text,
                status=style.status,
                core_output_artifact_id=core_stage.plan_artifact.artifact_id,
                final_output_artifact_id=style.artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                reviewer_ran=True,
                nutrition_repaired=nutrition_repaired,
                core_repaired=True,
                used_neutral_fallback=style.status is InvocationStatus.DEGRADED,
                failure_code=style.failure_code,
            )
        return await self._stages.safe_fallback(
            request=request,
            selection=selection,
            plan_artifact=core_stage.plan_artifact,
            verdict_artifact=review.artifact,
            calls=calls,
            tokens=tokens,
            failure_code=review.failure_code or "review_rejected_after_owner_repair",
            nutrition_repaired=nutrition_repaired,
            core_repaired=True,
        )


__all__ = ["OwnerRepairCoordinator"]
