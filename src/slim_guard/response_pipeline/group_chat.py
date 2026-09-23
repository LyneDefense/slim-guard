"""Group-chat reply composition using the shared response execution stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from slim_guard.agents.contracts import (
    ContentBlockKind,
    ProfessionalAssessment,
    RepairTarget,
    ReviewerVerdictStatus,
)
from slim_guard.group_chat.composer import compose_messages
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.planning import PlannedResponse, ResponsePlanBuilder
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile
from slim_guard.response_pipeline.repair import OwnerRepairCoordinator
from slim_guard.response_pipeline.stages import (
    CoreRepairFailure,
    ResponseStageExecutor,
    ReviewStage,
    StyleStage,
)
from slim_guard.response_pipeline.style_repair import repair_style_once
from slim_guard.runtime.contracts import AgentArtifact, ArtifactProducerRole, InvocationStatus


@dataclass(frozen=True, slots=True)
class ContentRepairOrigin:
    style: StyleStage
    review: ReviewStage


class GroupChatFinalizer:
    """Compose participant messages; role selection remains with Conversation Agent."""

    def __init__(
        self,
        *,
        stages: ResponseStageExecutor,
        plan_builder: ResponsePlanBuilder,
        owner_repairs: OwnerRepairCoordinator,
    ) -> None:
        self._stages = stages
        self._plan_builder = plan_builder
        self._owner_repairs = owner_repairs

    async def finalize(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        plan_artifact: AgentArtifact,
        selection: ResolvedStyleProfile,
        resolution_artifact: AgentArtifact,
        repair_origin: ContentRepairOrigin | None = None,
    ) -> ResponseFinalizationResult:
        attempt = 1 if repair_origin is None else 2
        pending = any(
            block.kind in {ContentBlockKind.QUESTION, ContentBlockKind.UNCERTAINTY}
            for block in planned.plan.content_blocks
        )
        successful_tool_results = self._successful_tool_results(request.tool_outcomes)
        tool_failed = self._has_failed_tool(request.tool_outcomes)
        routed = await self._stages.run_conversation(
            request=request,
            system_text=request.neutral_draft,
            plan_artifact=plan_artifact,
            parent_invocation_id=request.core_invocation_id,
            semantic_summary={
                "response_plan": planned.plan.model_dump(mode="json"),
                "professional_assessment": (
                    self._approved_assessment_summary(planned.assessment)
                    if planned.assessment is not None
                    else None
                ),
                "successful_tool_results": successful_tool_results,
            },
            allowed_source_refs=tuple(
                dict.fromkeys(
                    (
                        *planned.plan.citation_refs,
                        *(
                            reference
                            for block in planned.plan.content_blocks
                            for reference in block.source_refs
                        ),
                        *(
                            outcome.execution.tool_call_id
                            for outcome in request.tool_outcomes
                            if outcome.execution.result.status.value == "succeeded"
                        ),
                    )
                )
            ),
            pending_clarification=pending,
            tool_failed=tool_failed,
            attempt=attempt,
        )
        calls = routed.model_call_count
        tokens = routed.total_token_count
        coach = routed.routing.coach
        if coach is None:
            messages = compose_messages(
                turn_id=request.turn_id,
                system_text=routed.routing.system_text,
                system_source_refs=self._system_source_refs(planned),
                coach_text=None,
                tool_outcomes=request.tool_outcomes,
            )
            return ResponseFinalizationResult(
                text=routed.routing.system_text,
                status=routed.status,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=routed.artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                messages=messages,
                failure_code=routed.failure_code,
            )

        coach_planned = self._plan_builder.build(
            neutral_draft=coach.text,
            tool_outcomes=(),
            extra_source_refs=coach.source_refs,
        )
        coach_planned = replace(
            coach_planned,
            supporting_assessment=planned.assessment,
            assessment_artifact_id=planned.assessment_artifact_id,
        )
        coach_neutral_artifact = self._stages.artifact(
            request,
            producer=ArtifactProducerRole.CONVERSATION,
            artifact_type="coach_neutral_response",
            payload={
                "text": coach.text,
                "source_refs": coach.source_refs,
                "selected_by": "conversation_agent",
            },
            parents=(routed.artifact.artifact_id,),
        )
        await self._stages.persist(coach_neutral_artifact)
        style = await self._stages.run_style(
            request=request,
            planned=coach_planned,
            selection=selection,
            plan_artifact=plan_artifact,
            neutral_artifact=coach_neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=routed.invocation_id,
            attempt=attempt,
            prior_artifact_id=(repair_origin.style.artifact.artifact_id if repair_origin else None),
            verdict_artifact_id=(
                repair_origin.review.artifact.artifact_id if repair_origin else None
            ),
        )
        calls += style.model_call_count
        tokens += style.total_token_count
        review = await self._stages.run_review(
            request=request,
            planned=coach_planned,
            selection=selection,
            styled=style.response,
            styled_artifact=style.artifact,
            plan_artifact=plan_artifact,
            parent_invocation_id=style.invocation_id,
            attempt=attempt,
            prior_verdict_artifact_id=(
                repair_origin.review.artifact.artifact_id if repair_origin else None
            ),
        )
        calls += review.model_call_count
        tokens += review.total_token_count
        if (
            repair_origin is None
            and review.status is InvocationStatus.SUCCEEDED
            and review.verdict.verdict is ReviewerVerdictStatus.REPAIR
            and review.verdict.repair_target in {RepairTarget.CORE, RepairTarget.NUTRITION_EXPERT}
        ):
            return await self._repair_content(
                request=request,
                planned=planned,
                plan_artifact=plan_artifact,
                selection=selection,
                resolution_artifact=resolution_artifact,
                origin=ContentRepairOrigin(style=style, review=review),
                system_text=routed.routing.system_text,
                calls=calls,
                tokens=tokens,
            )
        style_repaired = False
        if (
            repair_origin is None
            and review.status is InvocationStatus.SUCCEEDED
            and review.verdict.verdict is ReviewerVerdictStatus.REPAIR
            and review.verdict.repair_target is RepairTarget.RESPONSE_STYLE
        ):
            style, review = await repair_style_once(
                stages=self._stages,
                request=request,
                planned=coach_planned,
                selection=selection,
                plan_artifact=plan_artifact,
                neutral_artifact=coach_neutral_artifact,
                resolution_artifact=resolution_artifact,
                first_style=style,
                first_review=review,
            )
            calls += style.model_call_count + review.model_call_count
            tokens += style.total_token_count + review.total_token_count
            style_repaired = True
        if (
            review.status is not InvocationStatus.SUCCEEDED
            or review.verdict.verdict is not ReviewerVerdictStatus.PASS
        ):
            messages = compose_messages(
                turn_id=request.turn_id,
                system_text=routed.routing.system_text,
                system_source_refs=self._system_source_refs(planned),
                coach_text=None,
                tool_outcomes=request.tool_outcomes,
            )
            return ResponseFinalizationResult(
                text=routed.routing.system_text,
                status=InvocationStatus.DEGRADED,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=review.artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                reviewer_ran=True,
                style_repaired=style_repaired,
                messages=messages,
                failure_code=review.failure_code
                or (
                    "coach_message_rejected_after_style_repair"
                    if style_repaired
                    else "coach_message_rejected"
                ),
            )
        messages = compose_messages(
            turn_id=request.turn_id,
            system_text=routed.routing.system_text,
            system_source_refs=self._system_source_refs(planned),
            coach_text=style.response.text,
            coach_source_refs=coach.source_refs,
            style_profile_version=selection.snapshot.profile.version,
            tool_outcomes=request.tool_outcomes,
        )
        return ResponseFinalizationResult(
            text="\n".join(item.text for item in messages if item.text),
            status=style.status,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=style.artifact.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=True,
            style_repaired=style_repaired,
            messages=messages,
            failure_code=style.failure_code,
        )

    async def _repair_content(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        plan_artifact: AgentArtifact,
        selection: ResolvedStyleProfile,
        resolution_artifact: AgentArtifact,
        origin: ContentRepairOrigin,
        system_text: str,
        calls: int,
        tokens: int,
    ) -> ResponseFinalizationResult:
        # Repair the complete content plan, not the coach-only expression plan.
        repair = await self._owner_repairs.repair(
            request=request,
            planned=planned,
            plan_artifact=plan_artifact,
            review=origin.review,
        )
        calls += repair.model_call_count
        tokens += repair.total_token_count
        repaired = repair.core
        if isinstance(repaired, CoreRepairFailure):
            return ResponseFinalizationResult(
                text=system_text,
                status=InvocationStatus.DEGRADED,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=origin.review.artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                reviewer_ran=True,
                failure_code=repaired.failure_code,
                nutrition_repaired=repair.nutrition_repaired,
                messages=compose_messages(
                    turn_id=request.turn_id,
                    system_text=system_text,
                    system_source_refs=self._system_source_refs(planned),
                    coach_text=None,
                    tool_outcomes=request.tool_outcomes,
                ),
            )
        result = await self.finalize(
            request=replace(
                request,
                neutral_draft=str(repaired.neutral_artifact.payload["text"]),
                core_invocation_id=repaired.invocation_id,
            ),
            planned=repaired.planned,
            plan_artifact=repaired.plan_artifact,
            selection=selection,
            resolution_artifact=resolution_artifact,
            repair_origin=origin,
        )
        return replace(
            result,
            model_call_count=calls + result.model_call_count,
            total_token_count=tokens + result.total_token_count,
            reviewer_ran=True,
            core_repaired=True,
            nutrition_repaired=repair.nutrition_repaired,
        )

    @staticmethod
    def _successful_tool_results(
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> tuple[dict[str, object], ...]:
        """Expose verified observations without making routing decisions.

        The participant model receives successful tool observations as data.  It
        decides whether any of them justify a coach message; this method does
        not classify a fixed set of business tools as coach-worthy.
        """

        summary: list[dict[str, object]] = []
        for outcome in outcomes:
            execution = outcome.execution
            if execution.result.status.value != "succeeded":
                continue
            summary.append(
                {
                    "tool_name": execution.tool_name,
                    "source_ref": execution.tool_call_id,
                    "result": GroupChatFinalizer._model_visible_result(execution.result.output),
                }
            )
        return tuple(summary)

    @staticmethod
    def _has_failed_tool(outcomes: tuple[ToolCallOutcome, ...]) -> bool:
        """Expose failed tool state to conversation routing as a fact.

        The Conversation Agent may still explain the failure, but it must not
        produce a coach acknowledgement that could be mistaken for success.
        This is derived from tool receipts rather than message text.
        """

        return any(outcome.execution.result.status.value != "succeeded" for outcome in outcomes)

    @staticmethod
    def _system_source_refs(planned: PlannedResponse) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *planned.plan.citation_refs,
                    *(
                        reference
                        for block in planned.plan.content_blocks
                        for reference in block.source_refs
                    ),
                )
            )
        )

    @staticmethod
    def _approved_assessment_summary(
        assessment: ProfessionalAssessment,
    ) -> dict[str, object]:
        """Expose only approved semantic conclusions to Conversation Agent.

        Citation objects and retrieval metadata stay in Trace and the nutrition
        artifact. The conversation model receives the conclusions it may express,
        never the source text or raw RAG record.
        """

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

    @classmethod
    def _model_visible_result(cls, value: Any) -> Any:
        """Remove persistence plumbing before observations reach a model."""

        if isinstance(value, dict):
            return {
                key: cls._model_visible_result(item)
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
            return [cls._model_visible_result(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._model_visible_result(item) for item in value)
        return value
