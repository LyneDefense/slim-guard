"""Shared response preparation and the remaining legacy whole-reply path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from slim_guard.agents.conversation import ConversationAgent
from slim_guard.agents.core import CoreResponseRepairAgent
from slim_guard.agents.nutrition import NutritionSpecialist
from slim_guard.agents.reviewer import ResponseReviewerAgent, ReviewerContextCompiler
from slim_guard.agents.style import ResponseStyleAgent, StyleContextCompiler
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.group_chat import GroupChatFinalizer
from slim_guard.response_pipeline.planning import ResponsePlanBuilder
from slim_guard.response_pipeline.profiles import (
    StyleProfileResolver,
)
from slim_guard.response_pipeline.repair import OwnerRepairCoordinator
from slim_guard.response_pipeline.stages import (
    ResponseStageExecutor,
)
from slim_guard.runtime.contracts import (
    ArtifactProducerRole,
)
from slim_guard.runtime.invocation import InvocationStore


class AgentResponseFinalizer:
    """Freeze response inputs, then compose the current group-chat architecture."""

    def __init__(
        self,
        *,
        style_agent: ResponseStyleAgent,
        profile_resolver: StyleProfileResolver,
        persistence: InvocationStore,
        recorder: HarnessRunRecorder,
        graph_version: str,
        reviewer_agent: ResponseReviewerAgent,
        plan_builder: ResponsePlanBuilder | None = None,
        style_compiler: StyleContextCompiler | None = None,
        reviewer_compiler: ReviewerContextCompiler | None = None,
        core_repair_agent: CoreResponseRepairAgent | None = None,
        nutrition_specialist: NutritionSpecialist | None = None,
        clock: Callable[[], datetime] | None = None,
        conversation_agent: ConversationAgent,
    ) -> None:
        self._profile_resolver = profile_resolver
        self._plan_builder = plan_builder or ResponsePlanBuilder()
        self._stages = ResponseStageExecutor(
            style_agent=style_agent,
            reviewer_agent=reviewer_agent,
            core_repair_agent=core_repair_agent,
            persistence=persistence,
            recorder=recorder,
            graph_version=graph_version,
            plan_builder=self._plan_builder,
            style_compiler=style_compiler or StyleContextCompiler(),
            reviewer_compiler=reviewer_compiler or ReviewerContextCompiler(),
            clock=clock,
            conversation_agent=conversation_agent,
        )
        self._group_chat = GroupChatFinalizer(
            stages=self._stages,
            plan_builder=self._plan_builder,
            owner_repairs=OwnerRepairCoordinator(
                stages=self._stages,
                nutrition_specialist=nutrition_specialist,
            ),
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
                "example_ids": [example.example_id for example in selection.snapshot.examples],
            },
            parents=(plan_artifact.artifact_id,),
        )
        await self._stages.persist(resolution_artifact)

        return await self._group_chat.finalize(
            request=request,
            planned=planned,
            plan_artifact=plan_artifact,
            selection=selection,
            resolution_artifact=resolution_artifact,
        )
