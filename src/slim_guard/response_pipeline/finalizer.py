"""Single normal response path: Core plan -> style -> fidelity review -> output."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from slim_guard.agents.contracts import (
    RepairTarget,
    ReviewerVerdict,
    ReviewerVerdictStatus,
    StyledResponse,
)
from slim_guard.agents.reviewer import (
    RESPONSE_REVIEWER_PROMPT_VERSION,
    ResponseReviewerAgent,
    ReviewerContextCompiler,
)
from slim_guard.agents.style import (
    RESPONSE_STYLE_PROMPT_VERSION,
    ResponseStyleAgent,
    StyleContextCompiler,
)
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.planning import PlannedResponse, ResponsePlanBuilder
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile, StyleProfileResolver
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationGrant, InvocationStore

SAFE_REVIEW_FALLBACK = "这条回复还需要进一步核对，我先不下结论。"


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._style_agent = style_agent
        self._profile_resolver = profile_resolver
        self._persistence = persistence
        self._recorder = recorder
        self._graph_version = graph_version
        self._max_invocation_tokens = max_invocation_tokens
        self._reviewer_agent = reviewer_agent
        self._reviewer_enabled = reviewer_enabled
        self._plan_builder = plan_builder or ResponsePlanBuilder()
        self._style_compiler = style_compiler or StyleContextCompiler()
        self._reviewer_compiler = reviewer_compiler or ReviewerContextCompiler()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def finalize(
        self,
        request: ResponseFinalizationRequest,
    ) -> ResponseFinalizationResult:
        planned = self._plan_builder.build(
            neutral_draft=request.neutral_draft,
            tool_outcomes=request.tool_outcomes,
        )
        plan_artifact = self._artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="response_plan",
            payload=planned.plan.model_dump(mode="json"),
            parents=self._optional_ids(planned.assessment_artifact_id),
        )
        await self._persist(plan_artifact, invocation_id=request.core_invocation_id)
        neutral_artifact = self._artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="neutral_response",
            payload={"text": request.neutral_draft},
            parents=(plan_artifact.artifact_id,),
        )
        await self._persist(neutral_artifact, invocation_id=request.core_invocation_id)

        selection = await self._profile_resolver.resolve()
        resolution_artifact = self._artifact(
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
        await self._persist(resolution_artifact)

        first_style = await self._run_style(
            request=request,
            planned=planned,
            selection=selection,
            plan_artifact=plan_artifact,
            neutral_artifact=neutral_artifact,
            resolution_artifact=resolution_artifact,
            parent_invocation_id=request.core_invocation_id,
            attempt=1,
        )
        calls = first_style[2]
        tokens = first_style[3]
        styled = first_style[0]
        styled_artifact = first_style[1]
        style_status = first_style[4]
        style_failure = first_style[5]

        if not self._should_review(planned):
            return ResponseFinalizationResult(
                text=styled.text,
                status=style_status,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=styled_artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                used_neutral_fallback=style_status is InvocationStatus.DEGRADED,
                failure_code=style_failure,
            )

        first_review = await self._run_review(
            request=request,
            planned=planned,
            selection=selection,
            styled=styled,
            styled_artifact=styled_artifact,
            plan_artifact=plan_artifact,
            parent_invocation_id=first_style[6],
            attempt=1,
        )
        calls += first_review[2]
        tokens += first_review[3]
        verdict = first_review[0]
        review_failure = first_review[4]
        if first_review[5] is InvocationStatus.SUCCEEDED and (
            verdict.verdict is ReviewerVerdictStatus.PASS
        ):
            return ResponseFinalizationResult(
                text=styled.text,
                status=style_status,
                core_output_artifact_id=plan_artifact.artifact_id,
                final_output_artifact_id=styled_artifact.artifact_id,
                style_profile_version=selection.snapshot.profile.version,
                model_call_count=calls,
                total_token_count=tokens,
                reviewer_ran=True,
                used_neutral_fallback=style_status is InvocationStatus.DEGRADED,
                failure_code=style_failure,
            )

        if (
            verdict.verdict is ReviewerVerdictStatus.REPAIR
            and verdict.repair_target is RepairTarget.RESPONSE_STYLE
            and first_review[5] is InvocationStatus.SUCCEEDED
        ):
            feedback = self._review_feedback(verdict)
            repaired_style = await self._run_style(
                request=request,
                planned=planned,
                selection=selection,
                plan_artifact=plan_artifact,
                neutral_artifact=neutral_artifact,
                resolution_artifact=resolution_artifact,
                parent_invocation_id=first_review[6],
                attempt=2,
                review_feedback=feedback,
                prior_artifact_id=styled_artifact.artifact_id,
                verdict_artifact_id=first_review[1].artifact_id,
            )
            calls += repaired_style[2]
            tokens += repaired_style[3]
            repaired = repaired_style[0]
            repaired_artifact = repaired_style[1]
            second_review = await self._run_review(
                request=request,
                planned=planned,
                selection=selection,
                styled=repaired,
                styled_artifact=repaired_artifact,
                plan_artifact=plan_artifact,
                parent_invocation_id=repaired_style[6],
                attempt=2,
                prior_verdict_artifact_id=first_review[1].artifact_id,
            )
            calls += second_review[2]
            tokens += second_review[3]
            if second_review[5] is InvocationStatus.SUCCEEDED and (
                second_review[0].verdict is ReviewerVerdictStatus.PASS
            ):
                return ResponseFinalizationResult(
                    text=repaired.text,
                    status=repaired_style[4],
                    core_output_artifact_id=plan_artifact.artifact_id,
                    final_output_artifact_id=repaired_artifact.artifact_id,
                    style_profile_version=selection.snapshot.profile.version,
                    model_call_count=calls,
                    total_token_count=tokens,
                    reviewer_ran=True,
                    style_repaired=True,
                    used_neutral_fallback=(
                        repaired_style[4] is InvocationStatus.DEGRADED
                    ),
                    failure_code=repaired_style[5],
                )
            review_failure = second_review[4] or "review_rejected_after_style_repair"

        fallback = self._artifact(
            request,
            producer=ArtifactProducerRole.RESPONSE_REVIEWER,
            artifact_type="safe_review_fallback",
            payload={
                "text": SAFE_REVIEW_FALLBACK,
                "reason_code": review_failure or "review_rejected",
            },
            parents=(plan_artifact.artifact_id, first_review[1].artifact_id),
        )
        await self._persist(fallback)
        return ResponseFinalizationResult(
            text=SAFE_REVIEW_FALLBACK,
            status=InvocationStatus.DEGRADED,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=fallback.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=True,
            style_repaired=(verdict.repair_target is RepairTarget.RESPONSE_STYLE),
            used_neutral_fallback=True,
            failure_code=review_failure or "review_rejected",
        )

    def _should_review(self, planned: PlannedResponse) -> bool:
        return (
            self._reviewer_enabled
            and self._reviewer_agent is not None
            and planned.assessment is not None
        )

    async def _run_style(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        selection: ResolvedStyleProfile,
        plan_artifact: AgentArtifact,
        neutral_artifact: AgentArtifact,
        resolution_artifact: AgentArtifact,
        parent_invocation_id: str,
        attempt: int,
        review_feedback: tuple[str, ...] = (),
        prior_artifact_id: str | None = None,
        verdict_artifact_id: str | None = None,
    ) -> tuple[
        StyledResponse,
        AgentArtifact,
        int,
        int,
        InvocationStatus,
        str | None,
        str,
    ]:
        parents = self._unique_ids(
            plan_artifact.artifact_id,
            neutral_artifact.artifact_id,
            resolution_artifact.artifact_id,
            planned.assessment_artifact_id,
            prior_artifact_id,
            verdict_artifact_id,
        )
        invocation = self._invocation(
            request=request,
            role=AgentRole.RESPONSE_STYLE,
            version=RESPONSE_STYLE_PROMPT_VERSION,
            input_schema="StyleContext",
            privacy_scopes=("response_plan", "style_profile", "style_examples"),
            parent_invocation_id=parent_invocation_id,
            input_artifact_ids=parents,
            attempt=attempt,
        )
        await self._start(invocation, "使用本轮已冻结的唯一风格 Profile 表达内容")
        context = self._style_compiler.compile(
            turn_id=request.turn_id,
            response_plan=planned.plan,
            profile=selection.snapshot.profile,
            assessment=planned.assessment,
            examples=selection.snapshot.for_act(planned.plan.communication_act),
        )
        result = await self._style_agent.run(
            invocation=invocation,
            context=context,
            grant=self._grant(invocation),
            review_feedback=review_feedback,
        )
        artifact = self._artifact(
            request,
            producer=ArtifactProducerRole.RESPONSE_STYLE,
            artifact_type=("neutral_response" if result.used_fallback else "styled_response"),
            payload={
                **result.response.model_dump(mode="json"),
                "attempt": attempt,
                "neutral_text": request.neutral_draft,
                "used_fallback": result.used_fallback,
                "failure_code": result.failure_code,
            },
            parents=parents,
        )
        await self._persist(artifact, invocation_id=invocation.invocation_id)
        await self._complete(
            invocation,
            result=AgentResult(
                invocation_id=invocation.invocation_id,
                status=result.status,
                output_schema="StyledResponse",
                output_schema_version="1",
                artifact_id=artifact.artifact_id,
                model_call_count=result.model_call_count,
                tool_call_count=0,
                token_usage=result.total_token_count,
                failure_code=result.failure_code,
            ),
        )
        return (
            result.response,
            artifact,
            result.model_call_count,
            result.total_token_count,
            result.status,
            result.failure_code,
            invocation.invocation_id,
        )

    async def _run_review(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        selection: ResolvedStyleProfile,
        styled: StyledResponse,
        styled_artifact: AgentArtifact,
        plan_artifact: AgentArtifact,
        parent_invocation_id: str,
        attempt: int,
        prior_verdict_artifact_id: str | None = None,
    ) -> tuple[
        ReviewerVerdict,
        AgentArtifact,
        int,
        int,
        str | None,
        InvocationStatus,
        str,
    ]:
        assert self._reviewer_agent is not None
        parents = self._unique_ids(
            plan_artifact.artifact_id,
            styled_artifact.artifact_id,
            planned.assessment_artifact_id,
            prior_verdict_artifact_id,
        )
        invocation = self._invocation(
            request=request,
            role=AgentRole.RESPONSE_REVIEWER,
            version=RESPONSE_REVIEWER_PROMPT_VERSION,
            input_schema="ReviewerContext",
            privacy_scopes=(
                "response_plan",
                "styled_response",
                "professional_assessment",
            ),
            parent_invocation_id=parent_invocation_id,
            input_artifact_ids=parents,
            attempt=attempt,
        )
        await self._start(invocation, "检查最终文案的安全性、证据和语义忠实度")
        context = self._reviewer_compiler.compile(
            turn_id=request.turn_id,
            response_plan=planned.plan,
            styled_response=styled,
            assessment=planned.assessment,
            style_profile=selection.snapshot.profile,
        )
        result = await self._reviewer_agent.run(
            invocation=invocation,
            context=context,
            grant=self._grant(invocation),
        )
        artifact = self._artifact(
            request,
            producer=ArtifactProducerRole.RESPONSE_REVIEWER,
            artifact_type="reviewer_verdict",
            payload={
                **result.verdict.model_dump(mode="json"),
                "attempt": attempt,
                "reviewed_artifact_ids": [
                    styled_artifact.artifact_id,
                    plan_artifact.artifact_id,
                ],
                "used_fallback": result.used_fallback,
                "failure_code": result.failure_code,
            },
            parents=parents,
        )
        await self._persist(artifact, invocation_id=invocation.invocation_id)
        await self._complete(
            invocation,
            result=AgentResult(
                invocation_id=invocation.invocation_id,
                status=result.status,
                output_schema="ReviewerVerdict",
                output_schema_version="1",
                artifact_id=artifact.artifact_id,
                model_call_count=result.model_call_count,
                tool_call_count=0,
                token_usage=result.total_token_count,
                failure_code=result.failure_code,
            ),
        )
        return (
            result.verdict,
            artifact,
            result.model_call_count,
            result.total_token_count,
            result.failure_code,
            result.status,
            invocation.invocation_id,
        )

    def _invocation(
        self,
        *,
        request: ResponseFinalizationRequest,
        role: AgentRole,
        version: str,
        input_schema: str,
        privacy_scopes: tuple[str, ...],
        parent_invocation_id: str,
        input_artifact_ids: tuple[str, ...],
        attempt: int,
    ) -> AgentInvocation:
        return AgentInvocation(
            invocation_id=f"inv-{uuid4()}",
            trace_id=request.trace_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            graph_version=self._graph_version,
            agent_role=role,
            agent_version=version,
            attempt=attempt,
            caller=AgentRole.CORE.value if attempt == 1 else AgentRole.RESPONSE_REVIEWER.value,
            parent_invocation_id=parent_invocation_id,
            input_artifact_ids=input_artifact_ids,
            input_schema=input_schema,
            privacy_scopes=privacy_scopes,
            deadline_at=request.deadline_at,
            max_model_calls=2,
            max_tool_calls=0,
            max_total_tokens=self._max_invocation_tokens,
        )

    @staticmethod
    def _grant(invocation: AgentInvocation) -> InvocationGrant:
        return InvocationGrant(
            agent_role=invocation.agent_role,
            allowed_tools=frozenset(),
            privacy_scopes=frozenset(invocation.privacy_scopes),
            max_model_calls=invocation.max_model_calls,
            max_tool_calls=0,
            max_total_tokens=invocation.max_total_tokens,
        )

    async def _start(self, invocation: AgentInvocation, reason: str) -> None:
        now = self._aware_now()
        await self._persistence.start_invocation(
            invocation,
            reason_summary=reason,
            started_at=now,
        )
        await self._recorder.record_workflow_event(
            turn_id=invocation.turn_id,
            event_type=ItemType.INVOCATION_STARTED,
            payload={
                "invocation_id": invocation.invocation_id,
                "agent_role": invocation.agent_role.value,
                "agent_version": invocation.agent_version,
                "attempt": invocation.attempt,
                "parent_invocation_id": invocation.parent_invocation_id,
                "input_artifact_ids": invocation.input_artifact_ids,
                "allowed_tool_names": invocation.allowed_tools,
                "privacy_scopes": invocation.privacy_scopes,
                "reason_summary": reason,
                "started_at": now,
            },
        )

    async def _complete(self, invocation: AgentInvocation, *, result: AgentResult) -> None:
        completed_at = self._aware_now()
        await self._persistence.complete_invocation(result, completed_at=completed_at)
        await self._recorder.record_workflow_event(
            turn_id=invocation.turn_id,
            event_type=ItemType.INVOCATION_RESULT,
            payload={
                "invocation_id": invocation.invocation_id,
                "status": result.status.value,
                "output_artifact_id": result.artifact_id,
                "model_call_count": result.model_call_count,
                "tool_call_count": result.tool_call_count,
                "total_token_count": result.token_usage,
                "failure_code": result.failure_code,
                "completed_at": completed_at,
            },
        )

    async def _persist(
        self,
        artifact: AgentArtifact,
        invocation_id: str | None = None,
    ) -> None:
        await self._persistence.append_artifact(artifact, invocation_id=invocation_id)
        await self._recorder.record_workflow_event(
            turn_id=artifact.turn_id,
            event_type=ItemType.ARTIFACT_CREATED,
            payload={
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type,
                "producer_role": artifact.producer_role.value,
                "schema_version": artifact.schema_version,
                "parent_artifact_ids": artifact.parent_artifact_ids,
                "payload_sha256": artifact.payload_sha256,
            },
        )

    def _artifact(
        self,
        request: ResponseFinalizationRequest,
        *,
        producer: ArtifactProducerRole,
        artifact_type: str,
        payload: dict[str, object],
        parents: tuple[str, ...] = (),
    ) -> AgentArtifact:
        return AgentArtifact.create(
            artifact_id=f"artifact-{uuid4()}",
            turn_id=request.turn_id,
            producer_role=producer,
            artifact_type=artifact_type,
            schema_version="1",
            parent_artifact_ids=parents,
            payload=payload,
            created_at=self._aware_now(),
        )

    @staticmethod
    def _review_feedback(verdict: ReviewerVerdict) -> tuple[str, ...]:
        values = [item.type.value for item in verdict.issues]
        if verdict.issue_type is not None:
            values.append(verdict.issue_type.value)
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _optional_ids(value: str | None) -> tuple[str, ...]:
        return (value,) if value else ()

    @staticmethod
    def _unique_ids(*values: str | None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value is not None))

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Response finalizer clock must be timezone-aware")
        return now


__all__ = ["AgentResponseFinalizer", "SAFE_REVIEW_FALLBACK"]
