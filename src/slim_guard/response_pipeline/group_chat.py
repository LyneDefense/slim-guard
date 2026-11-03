"""Group-chat reply composition using the shared response execution stages."""

from __future__ import annotations

from dataclasses import dataclass, replace

from slim_guard.agents.contracts import (
    ContentBlockKind,
    RepairTarget,
    ReviewerVerdictStatus,
)
from slim_guard.group_chat import COACH_ROLE_VIOLATION
from slim_guard.group_chat.composer import OutgoingTextMessage, compose_messages
from slim_guard.group_chat.contracts import ParticipantRole
from slim_guard.group_chat.failures import reply_failure_notice
from slim_guard.group_chat.routing import RoutedMessage
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.conversation_evidence import assessment_summary, tool_observations
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
    next_sequence: int


@dataclass(frozen=True, slots=True)
class CoachMessageResult:
    outgoing: OutgoingTextMessage | None
    style: StyleStage
    review: ReviewStage
    style_repaired: bool
    model_call_count: int
    total_token_count: int


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
        sequence_base = repair_origin.next_sequence if repair_origin else 0
        pending = any(
            block.kind in {ContentBlockKind.QUESTION, ContentBlockKind.UNCERTAINTY}
            for block in planned.plan.content_blocks
        )
        tool_failed = any(
            o.execution.result.status.value != "succeeded" for o in request.tool_outcomes
        )
        routed = await self._stages.run_conversation(
            request=request,
            neutral_draft=request.neutral_draft,
            conversation_context=request.conversation_context,
            plan_artifact=plan_artifact,
            parent_invocation_id=request.core_invocation_id,
            semantic_summary={
                "response_plan": planned.plan.model_dump(mode="json"),
                "professional_assessment": (
                    assessment_summary(planned.assessment)
                    if planned.assessment is not None
                    else None
                ),
                "tool_results": tool_observations(request.tool_outcomes),
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
                        *(outcome.execution.tool_call_id for outcome in request.tool_outcomes),
                    )
                )
            ),
            pending_clarification=pending,
            tool_failed=tool_failed,
            attempt=attempt,
        )
        calls = routed.model_call_count
        tokens = routed.total_token_count
        outgoing: list[OutgoingTextMessage] = []
        reviewer_ran = False
        style_repaired = False
        final_artifact_id = routed.artifact.artifact_id
        failure_codes: list[str] = list(routed.routing.suppressed_reasons)
        coach_index = 0
        for routed_message in routed.routing.messages:
            if routed_message.participant is ParticipantRole.SYSTEM_ASSISTANT:
                outgoing.append(
                    OutgoingTextMessage(
                        participant=ParticipantRole.SYSTEM_ASSISTANT,
                        text=routed_message.text,
                        source_refs=routed_message.source_refs,
                        requires_response=routed_message.requires_response,
                        notice_text=routed_message.notice_text,
                    )
                )
                continue

            coach_result = await self._finalize_coach_message(
                request=request,
                coach=routed_message,
                planned=planned,
                plan_artifact=plan_artifact,
                selection=selection,
                resolution_artifact=resolution_artifact,
                conversation_artifact=routed.artifact,
                conversation_invocation_id=routed.invocation_id,
                attempt=attempt,
                sequence=sequence_base + coach_index,
                repair_origin=repair_origin if coach_index == 0 else None,
            )
            coach_index += 1
            calls += coach_result.model_call_count
            tokens += coach_result.total_token_count
            reviewer_ran = True
            style_repaired = style_repaired or coach_result.style_repaired
            final_artifact_id = coach_result.review.artifact.artifact_id
            review = coach_result.review

            if (
                repair_origin is None
                and review.status is InvocationStatus.SUCCEEDED
                and review.verdict.verdict is ReviewerVerdictStatus.REPAIR
                and review.verdict.repair_target
                in {RepairTarget.CORE, RepairTarget.NUTRITION_EXPERT}
            ):
                return await self._repair_content(
                    request=request,
                    planned=planned,
                    plan_artifact=plan_artifact,
                    selection=selection,
                    resolution_artifact=resolution_artifact,
                    origin=ContentRepairOrigin(
                        style=coach_result.style,
                        review=review,
                        next_sequence=sequence_base + coach_index + 1,
                    ),
                    preserved_notices=routed.routing.system_messages,
                    calls=calls,
                    tokens=tokens,
                )

            if coach_result.outgoing is None:
                failure_codes.append(
                    review.failure_code
                    or coach_result.style.failure_code
                    or (
                        "coach_message_rejected_after_style_repair"
                        if coach_result.style_repaired
                        else "coach_message_rejected"
                    )
                )
                continue
            outgoing.append(coach_result.outgoing)
            if coach_result.style.status is not InvocationStatus.SUCCEEDED:
                failure_codes.append(coach_result.style.failure_code or "coach_style_degraded")

        if not outgoing:
            # A rejected doctor response must not reappear as an unreviewed
            # system draft. Completed operation cards are still projected below.
            outgoing.append(reply_failure_notice())
            failure_codes.append("no_adoptable_conversation_message")
        messages = compose_messages(
            turn_id=request.turn_id,
            text_messages=tuple(outgoing),
            tool_outcomes=request.tool_outcomes,
        )
        final_status = (
            InvocationStatus.DEGRADED
            if failure_codes or routed.status is InvocationStatus.DEGRADED
            else InvocationStatus.SUCCEEDED
        )
        return ResponseFinalizationResult(
            text="\n".join(item.text for item in messages if item.text),
            status=final_status,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=final_artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=reviewer_ran,
            style_repaired=style_repaired,
            messages=messages,
            failure_code=";".join(failure_codes) or routed.failure_code,
        )

    async def _finalize_coach_message(
        self,
        *,
        request: ResponseFinalizationRequest,
        coach: RoutedMessage,
        planned: PlannedResponse,
        plan_artifact: AgentArtifact,
        selection: ResolvedStyleProfile,
        resolution_artifact: AgentArtifact,
        conversation_artifact: AgentArtifact,
        conversation_invocation_id: str,
        attempt: int,
        sequence: int,
        repair_origin: ContentRepairOrigin | None,
    ) -> CoachMessageResult:
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
            parents=(conversation_artifact.artifact_id,),
        )
        await self._stages.persist(coach_neutral_artifact)
        style = await self._stages.run_style(
            request=request,
            planned=coach_planned,
            selection=selection,
            plan_artifact=plan_artifact,
            neutral_artifact=coach_neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=conversation_invocation_id,
            attempt=attempt,
            sequence=sequence,
            prior_artifact_id=(repair_origin.style.artifact.artifact_id if repair_origin else None),
            verdict_artifact_id=(
                repair_origin.review.artifact.artifact_id if repair_origin else None
            ),
        )
        review = await self._stages.run_review(
            request=request,
            planned=coach_planned,
            selection=selection,
            styled=style.response,
            styled_artifact=style.artifact,
            plan_artifact=plan_artifact,
            parent_invocation_id=style.invocation_id,
            attempt=attempt,
            sequence=sequence,
            prior_verdict_artifact_id=(
                repair_origin.review.artifact.artifact_id if repair_origin else None
            ),
        )
        model_call_count = style.model_call_count + review.model_call_count
        total_token_count = style.total_token_count + review.total_token_count
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
                sequence=sequence,
            )
            model_call_count += style.model_call_count + review.model_call_count
            total_token_count += style.total_token_count + review.total_token_count
            style_repaired = True
        outgoing = None
        if (
            review.status is InvocationStatus.SUCCEEDED
            and review.verdict.verdict is ReviewerVerdictStatus.PASS
            and style.failure_code != COACH_ROLE_VIOLATION
        ):
            outgoing = OutgoingTextMessage(
                participant=ParticipantRole.COACH,
                text=style.response.text,
                source_refs=coach.source_refs,
                style_profile_version=selection.snapshot.profile.version,
            )
        return CoachMessageResult(
            outgoing=outgoing,
            style=style,
            review=review,
            style_repaired=style_repaired,
            model_call_count=model_call_count,
            total_token_count=total_token_count,
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
        preserved_notices: tuple[RoutedMessage, ...],
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
            notices = tuple(
                OutgoingTextMessage(
                    participant=message.participant,
                    text=message.text,
                    source_refs=message.source_refs,
                    requires_response=message.requires_response,
                    notice_text=message.notice_text,
                )
                for message in preserved_notices
            ) or (reply_failure_notice(),)
            return ResponseFinalizationResult(
                text="\n".join(message.text for message in notices),
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
                    text_messages=notices,
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
