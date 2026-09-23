"""Bounded content repair; presentation is rebuilt by Conversation Agent."""

from dataclasses import dataclass

from slim_guard.agents.contracts import RepairTarget
from slim_guard.agents.nutrition import NutritionRepairRequest, NutritionSpecialist
from slim_guard.response_pipeline.contracts import ResponseFinalizationRequest
from slim_guard.response_pipeline.planning import PlannedResponse
from slim_guard.response_pipeline.stages import (
    CoreRepairFailure,
    CoreRepairStage,
    ResponseStageExecutor,
    ReviewStage,
)
from slim_guard.runtime.contracts import AgentArtifact


@dataclass(frozen=True, slots=True)
class ContentRepairResult:
    core: CoreRepairStage | CoreRepairFailure
    model_call_count: int
    total_token_count: int
    nutrition_repaired: bool = False


class OwnerRepairCoordinator:
    """Honor the reviewer's repair target without re-executing business tools."""

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
        plan_artifact: AgentArtifact,
        review: ReviewStage,
    ) -> ContentRepairResult:
        calls = tokens = 0
        assessment = planned.assessment
        assessment_id = planned.assessment_artifact_id
        parent = review.invocation_id
        nutrition_repaired = False
        if review.verdict.repair_target is RepairTarget.NUTRITION_EXPERT:
            if self._nutrition_specialist is None or assessment_id is None:
                return ContentRepairResult(
                    CoreRepairFailure("nutrition_repair_boundary_unavailable"), 0, 0
                )
            try:
                nutrition = await self._nutrition_specialist.repair(
                    NutritionRepairRequest(
                        trace_id=request.trace_id,
                        thread_id=request.thread_id,
                        turn_id=request.turn_id,
                        parent_invocation_id=parent,
                        assessment_artifact_id=assessment_id,
                        verdict_artifact_id=review.artifact.artifact_id,
                        deadline_at=request.deadline_at,
                    )
                )
            except (RuntimeError, ValueError):
                return ContentRepairResult(CoreRepairFailure("nutrition_repair_failed"), 0, 0)
            calls += nutrition.model_call_count
            tokens += nutrition.total_token_count
            assessment = nutrition.assessment
            assessment_id = nutrition.artifact.artifact_id
            parent = nutrition.invocation.invocation_id
            nutrition_repaired = True

        core = await self._stages.run_core_repair(
            request=request,
            planned=planned,
            original_plan_artifact=plan_artifact,
            assessment=assessment,
            assessment_artifact_id=assessment_id,
            verdict=review.verdict,
            verdict_artifact=review.artifact,
            parent_invocation_id=parent,
        )
        return ContentRepairResult(
            core=core,
            model_call_count=calls + core.model_call_count,
            total_token_count=tokens + core.total_token_count,
            nutrition_repaired=nutrition_repaired,
        )
