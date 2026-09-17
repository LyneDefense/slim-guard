"""Reusable execution stages for response planning, style, review, and Core repair."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from slim_guard.agents.contracts import (
    ProfessionalAssessment,
    ReviewerVerdict,
    StyledResponse,
)
from slim_guard.agents.core import (
    CORE_REPAIR_PROMPT_VERSION,
    CoreRepairContext,
    CoreResponseRepairAgent,
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
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile
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


@dataclass(frozen=True, slots=True)
class StyleStage:
    response: StyledResponse
    artifact: AgentArtifact
    model_call_count: int
    total_token_count: int
    status: InvocationStatus
    failure_code: str | None
    invocation_id: str


@dataclass(frozen=True, slots=True)
class ReviewStage:
    verdict: ReviewerVerdict
    artifact: AgentArtifact
    model_call_count: int
    total_token_count: int
    failure_code: str | None
    status: InvocationStatus
    invocation_id: str


@dataclass(frozen=True, slots=True)
class CoreRepairStage:
    planned: PlannedResponse
    plan_artifact: AgentArtifact
    neutral_artifact: AgentArtifact
    invocation_id: str
    model_call_count: int
    total_token_count: int


class ResponseStageExecutor:
    """Run bounded response stages and persist their immutable audit records."""

    def __init__(
        self,
        *,
        style_agent: ResponseStyleAgent,
        reviewer_agent: ResponseReviewerAgent | None,
        core_repair_agent: CoreResponseRepairAgent | None,
        persistence: InvocationStore,
        recorder: HarnessRunRecorder,
        graph_version: str,
        max_invocation_tokens: int,
        plan_builder: ResponsePlanBuilder,
        style_compiler: StyleContextCompiler,
        reviewer_compiler: ReviewerContextCompiler,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._style_agent = style_agent
        self._reviewer_agent = reviewer_agent
        self._core_repair_agent = core_repair_agent
        self._persistence = persistence
        self._recorder = recorder
        self._graph_version = graph_version
        self._max_invocation_tokens = max_invocation_tokens
        self._plan_builder = plan_builder
        self._style_compiler = style_compiler
        self._reviewer_compiler = reviewer_compiler
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_style(
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
    ) -> StyleStage:
        parents = self.unique_ids(
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
        artifact = self.artifact(
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
        await self.persist(artifact, invocation_id=invocation.invocation_id)
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
        return StyleStage(
            response=result.response,
            artifact=artifact,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
            status=result.status,
            failure_code=result.failure_code,
            invocation_id=invocation.invocation_id,
        )

    async def run_review(
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
    ) -> ReviewStage:
        if self._reviewer_agent is None:
            raise RuntimeError("Response review stage is not configured")
        parents = self.unique_ids(
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
        artifact = self.artifact(
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
        await self.persist(artifact, invocation_id=invocation.invocation_id)
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
        return ReviewStage(
            verdict=result.verdict,
            artifact=artifact,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
            failure_code=result.failure_code,
            status=result.status,
            invocation_id=invocation.invocation_id,
        )

    async def run_core_repair(
        self,
        *,
        request: ResponseFinalizationRequest,
        planned: PlannedResponse,
        original_plan_artifact: AgentArtifact,
        assessment: ProfessionalAssessment | None,
        assessment_artifact_id: str | None,
        verdict: ReviewerVerdict,
        verdict_artifact: AgentArtifact,
        parent_invocation_id: str,
    ) -> CoreRepairStage | None:
        if self._core_repair_agent is None:
            return None
        invocation = self._invocation(
            request=request,
            role=AgentRole.CORE,
            version=CORE_REPAIR_PROMPT_VERSION,
            input_schema="CoreRepairContext",
            privacy_scopes=("response_plan", "professional_assessment"),
            parent_invocation_id=parent_invocation_id,
            input_artifact_ids=self.unique_ids(
                original_plan_artifact.artifact_id,
                verdict_artifact.artifact_id,
                assessment_artifact_id,
            ),
            attempt=2,
        )
        await self._start(invocation, "按审查结果修复任务理解或重建中性内容稿")
        result = await self._core_repair_agent.run(
            invocation=invocation,
            context=CoreRepairContext(
                turn_id=request.turn_id,
                original_neutral_draft=request.neutral_draft,
                response_plan=planned.plan,
                assessment=assessment,
                issue_types=self.review_feedback(verdict),
                repair_instruction=(
                    verdict.reason_summary
                    or "按结构化问题类型修复内容，并保留所有已验证事实。"
                ),
            ),
            grant=self._grant(invocation),
        )
        if result.draft is None:
            await self._complete(
                invocation,
                result=AgentResult(
                    invocation_id=invocation.invocation_id,
                    status=InvocationStatus.FAILED,
                    output_schema="CoreRepairDraft",
                    output_schema_version="1",
                    model_call_count=result.model_call_count,
                    tool_call_count=0,
                    token_usage=result.total_token_count,
                    failure_code=result.failure_code or "core_repair_failed",
                ),
            )
            return None
        repaired = self._plan_builder.build_with_assessment(
            neutral_draft=result.draft.neutral_draft,
            assessment=assessment,
            assessment_artifact_id=assessment_artifact_id,
            tool_outcomes=request.tool_outcomes,
        )
        parents = self.unique_ids(
            original_plan_artifact.artifact_id,
            verdict_artifact.artifact_id,
            assessment_artifact_id,
        )
        plan_artifact = self.artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="response_plan",
            payload={
                **repaired.plan.model_dump(mode="json"),
                "attempt": 2,
                "repair_feedback": list(self.review_feedback(verdict)),
            },
            parents=parents,
        )
        await self.persist(plan_artifact, invocation_id=invocation.invocation_id)
        neutral_artifact = self.artifact(
            request,
            producer=ArtifactProducerRole.CORE,
            artifact_type="neutral_response",
            payload={"text": result.draft.neutral_draft, "attempt": 2},
            parents=(plan_artifact.artifact_id,),
        )
        await self.persist(neutral_artifact, invocation_id=invocation.invocation_id)
        await self._complete(
            invocation,
            result=AgentResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.SUCCEEDED,
                output_schema="ResponsePlan",
                output_schema_version="1",
                artifact_id=plan_artifact.artifact_id,
                model_call_count=result.model_call_count,
                tool_call_count=0,
                token_usage=result.total_token_count,
            ),
        )
        return CoreRepairStage(
            planned=repaired,
            plan_artifact=plan_artifact,
            neutral_artifact=neutral_artifact,
            invocation_id=invocation.invocation_id,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
        )

    async def safe_fallback(
        self,
        *,
        request: ResponseFinalizationRequest,
        selection: ResolvedStyleProfile,
        plan_artifact: AgentArtifact,
        verdict_artifact: AgentArtifact,
        calls: int,
        tokens: int,
        failure_code: str,
        style_repaired: bool = False,
        nutrition_repaired: bool = False,
        core_repaired: bool = False,
    ) -> ResponseFinalizationResult:
        fallback = self.artifact(
            request,
            producer=ArtifactProducerRole.RESPONSE_REVIEWER,
            artifact_type="safe_review_fallback",
            payload={"text": SAFE_REVIEW_FALLBACK, "reason_code": failure_code},
            parents=(plan_artifact.artifact_id, verdict_artifact.artifact_id),
        )
        await self.persist(fallback)
        return ResponseFinalizationResult(
            text=SAFE_REVIEW_FALLBACK,
            status=InvocationStatus.DEGRADED,
            core_output_artifact_id=plan_artifact.artifact_id,
            final_output_artifact_id=fallback.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=True,
            style_repaired=style_repaired,
            nutrition_repaired=nutrition_repaired,
            core_repaired=core_repaired,
            used_neutral_fallback=True,
            failure_code=failure_code,
        )

    def artifact(
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

    async def persist(
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

    @staticmethod
    def review_feedback(verdict: ReviewerVerdict) -> tuple[str, ...]:
        values = [item.type.value for item in verdict.issues]
        if verdict.issue_type is not None:
            values.append(verdict.issue_type.value)
        return tuple(dict.fromkeys(values))

    @staticmethod
    def optional_ids(value: str | None) -> tuple[str, ...]:
        return (value,) if value else ()

    @staticmethod
    def unique_ids(*values: str | None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value is not None))

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
            caller=(
                AgentRole.CORE.value
                if attempt == 1
                else AgentRole.RESPONSE_REVIEWER.value
            ),
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

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Response stage clock must be timezone-aware")
        return now


__all__ = [
    "CoreRepairStage",
    "ReviewStage",
    "SAFE_REVIEW_FALLBACK",
    "ResponseStageExecutor",
    "StyleStage",
]
