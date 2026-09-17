"""Single normal response path: Core plan -> style -> fidelity review -> output."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from slim_guard.agents.contracts import RepairTarget, ReviewerVerdictStatus
from slim_guard.agents.core import CoreResponseRepairAgent
from slim_guard.agents.nutrition import NutritionSpecialist
from slim_guard.agents.reviewer import ResponseReviewerAgent, ReviewerContextCompiler
from slim_guard.agents.style import ResponseStyleAgent, StyleContextCompiler
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.planning import PlannedResponse, ResponsePlanBuilder
from slim_guard.response_pipeline.profiles import (
    ResolvedStyleProfile,
    StyleProfileResolver,
)
from slim_guard.response_pipeline.repair import OwnerRepairCoordinator
from slim_guard.response_pipeline.stages import (
    SAFE_REVIEW_FALLBACK,
    ResponseStageExecutor,
    ReviewStage,
    StyleStage,
)
from slim_guard.runtime.contracts import (
    AgentArtifact,
    ArtifactProducerRole,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationStore


class AgentResponseFinalizer:
    """Apply the one active style profile without generating a competing answer."""

    def __init__(
        self,
        *,
        style_agent: ResponseStyleAgent,
        profile_resolver: StyleProfileResolver,
        persistence: InvocationStore,
        recorder: HarnessRunRecorder,
        graph_version: str,
        max_invocation_tokens: int,
        reviewer_agent: ResponseReviewerAgent | None = None,
        reviewer_enabled: bool = False,
        plan_builder: ResponsePlanBuilder | None = None,
        style_compiler: StyleContextCompiler | None = None,
        reviewer_compiler: ReviewerContextCompiler | None = None,
        core_repair_agent: CoreResponseRepairAgent | None = None,
        nutrition_specialist: NutritionSpecialist | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._profile_resolver = profile_resolver
        self._reviewer_enabled = reviewer_enabled
        self._reviewer_agent = reviewer_agent
        self._plan_builder = plan_builder or ResponsePlanBuilder()
        self._stages = ResponseStageExecutor(
            style_agent=style_agent,
            reviewer_agent=reviewer_agent,
            core_repair_agent=core_repair_agent,
            persistence=persistence,
            recorder=recorder,
            graph_version=graph_version,
            max_invocation_tokens=max_invocation_tokens,
            plan_builder=self._plan_builder,
            style_compiler=style_compiler or StyleContextCompiler(),
            reviewer_compiler=reviewer_compiler or ReviewerContextCompiler(),
            clock=clock,
        )
        self._owner_repairs = OwnerRepairCoordinator(
            stages=self._stages,
            nutrition_specialist=nutrition_specialist,
        )

    async def finalize(
        self,
        request: ResponseFinalizationRequest,
    ) -> ResponseFinalizationResult:
        planned = self._plan_builder.build(
            neutral_draft=request.neutral_draft,
            tool_outcomes=request.tool_outcomes,
        )
        plan_artifact = self._stages.artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="response_plan",
            payload=planned.plan.model_dump(mode="json"),
            parents=self._stages.optional_ids(planned.assessment_artifact_id),
        )
        await self._stages.persist(
            plan_artifact,
            invocation_id=request.core_invocation_id,
        )
        neutral_artifact = self._stages.artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="neutral_response",
            payload={"text": request.neutral_draft},
            parents=(plan_artifact.artifact_id,),
        )
        await self._stages.persist(
            neutral_artifact,
            invocation_id=request.core_invocation_id,
        )

        selection = await self._profile_resolver.resolve()
        resolution_artifact = self._stages.artifact(
            request,
            producer=ArtifactProducerRole.STYLE_RESOLVER,
            artifact_type="style_resolution",
            payload={
                "profile_id": selection.snapshot.profile.profile_id,
                "profile_version": selection.snapshot.profile.version,
                "requested_version": selection.requested_version,
                "source": selection.source,
                "fallback_reason": selection.fallback_reason,
                "example_ids": [
                    example.example_id
                    for example in selection.snapshot.for_act(
                        planned.plan.communication_act
                    )
                ],
            },
            parents=(plan_artifact.artifact_id,),
        )
        await self._stages.persist(resolution_artifact)

        first_style = await self._stages.run_style(
            request=request,
            planned=planned,
            selection=selection,
            plan_artifact=plan_artifact,
            neutral_artifact=neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=request.core_invocation_id,
            attempt=1,
        )
        calls = first_style.model_call_count
        tokens = first_style.total_token_count

        if not self._should_review(planned):
            return self._final_without_review(
                selection=selection,
                plan_artifact=plan_artifact,
                style=first_style,
            )

        first_review = await self._stages.run_review(
            request=request,
            planned=planned,
            selection=selection,
            styled=first_style.response,
            styled_artifact=first_style.artifact,
            plan_artifact=plan_artifact,
            parent_invocation_id=first_style.invocation_id,
            attempt=1,
        )
        calls += first_review.model_call_count
        tokens += first_review.total_token_count
        verdict = first_review.verdict
        if first_review.status is InvocationStatus.SUCCEEDED and (
            verdict.verdict is ReviewerVerdictStatus.PASS
        ):
            return self._final_after_review(
                selection=selection,
                plan_artifact=plan_artifact,
                style=first_style,
                calls=calls,
                tokens=tokens,
            )

        if (
            verdict.verdict is ReviewerVerdictStatus.REPAIR
            and verdict.repair_target is RepairTarget.RESPONSE_STYLE
            and first_review.status is InvocationStatus.SUCCEEDED
        ):
            return await self._repair_style(
                request=request,
                planned=planned,
                selection=selection,
                plan_artifact=plan_artifact,
                neutral_artifact=neutral_artifact,
                resolution_artifact=resolution_artifact,
                first_style=first_style,
                first_review=first_review,
                calls=calls,
                tokens=tokens,
            )

        if (
            verdict.verdict is ReviewerVerdictStatus.REPAIR
            and verdict.repair_target
            in {
                RepairTarget.CORE,
                RepairTarget.ORCHESTRATOR,
                RepairTarget.NUTRITION_EXPERT,
            }
            and first_review.status is InvocationStatus.SUCCEEDED
        ):
            return await self._owner_repairs.repair(
                request=request,
                planned=planned,
                selection=selection,
                original_plan_artifact=plan_artifact,
                prior_styled_artifact=first_style.artifact,
                resolution_artifact=resolution_artifact,
                verdict=verdict,
                verdict_artifact=first_review.artifact,
                reviewer_invocation_id=first_review.invocation_id,
                calls=calls,
                tokens=tokens,
            )

        return await self._stages.safe_fallback(
            request=request,
            selection=selection,
            plan_artifact=plan_artifact,
            verdict_artifact=first_review.artifact,
            calls=calls,
            tokens=tokens,
            failure_code=first_review.failure_code or "review_rejected",
        )

    async def _repair_style(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        selection: ResolvedStyleProfile,
        plan_artifact: AgentArtifact,
        neutral_artifact: AgentArtifact,
        resolution_artifact: AgentArtifact,
        first_style: StyleStage,
        first_review: ReviewStage,
        calls: int,
        tokens: int,
    ) -> ResponseFinalizationResult:
        repaired = await self._stages.run_style(
            request=request,
            planned=planned,
            selection=selection,
            plan_artifact=plan_artifact,
            neutral_artifact=neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=first_review.invocation_id,
            attempt=2,
            review_feedback=self._stages.review_feedback(first_review.verdict),
            prior_artifact_id=first_style.artifact.artifact_id,
            verdict_artifact_id=first_review.artifact.artifact_id,
        )
        calls += repaired.model_call_count
        tokens += repaired.total_token_count
        review = await self._stages.run_review(
            request=request,
            planned=planned,
            selection=selection,
            styled=repaired.response,
            styled_artifact=repaired.artifact,
            plan_artifact=plan_artifact,
            parent_invocation_id=repaired.invocation_id,
            attempt=2,
            prior_verdict_artifact_id=first_review.artifact.artifact_id,
        )
        calls += review.model_call_count
        tokens += review.total_token_count
        if review.status is InvocationStatus.SUCCEEDED and (
            review.verdict.verdict is ReviewerVerdictStatus.PASS
        ):
            return ResponseFinalizationResult(
                text=repaired.response.text,
                status=repaired.status,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=repaired.artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                reviewer_ran=True,
                style_repaired=True,
                used_neutral_fallback=repaired.status is InvocationStatus.DEGRADED,
                failure_code=repaired.failure_code,
            )
        return await self._stages.safe_fallback(
            request=request,
            selection=selection,
            plan_artifact=plan_artifact,
            verdict_artifact=review.artifact,
            calls=calls,
            tokens=tokens,
            failure_code=review.failure_code or "review_rejected_after_style_repair",
            style_repaired=True,
        )

    @staticmethod
    def _final_without_review(
        *,
        selection: ResolvedStyleProfile,
        plan_artifact: AgentArtifact,
        style: StyleStage,
    ) -> ResponseFinalizationResult:
        return ResponseFinalizationResult(
            text=style.response.text,
            status=style.status,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=style.artifact.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=style.model_call_count,
            total_token_count=style.total_token_count,
            used_neutral_fallback=style.status is InvocationStatus.DEGRADED,
            failure_code=style.failure_code,
        )

    @staticmethod
    def _final_after_review(
        *,
        selection: ResolvedStyleProfile,
        plan_artifact: AgentArtifact,
        style: StyleStage,
        calls: int,
        tokens: int,
    ) -> ResponseFinalizationResult:
        return ResponseFinalizationResult(
            text=style.response.text,
            status=style.status,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=style.artifact.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=True,
            used_neutral_fallback=style.status is InvocationStatus.DEGRADED,
            failure_code=style.failure_code,
        )

    def _should_review(self, planned: PlannedResponse) -> bool:
        return (
            self._reviewer_enabled
            and self._reviewer_agent is not None
            and planned.assessment is not None
        )


__all__ = ["AgentResponseFinalizer", "SAFE_REVIEW_FALLBACK"]
