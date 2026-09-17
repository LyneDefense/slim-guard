from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from slim_guard.agent_models.gateway import ModelGateway, ModelMessage, ModelResponse
from slim_guard.agents.core import CoreAgent
from slim_guard.harness.context import CompiledContext, ContextCompiler
from slim_guard.harness.context_data import ContextDataProvider, EmptyContextDataProvider
from slim_guard.harness.errors import ContextCompilationError
from slim_guard.harness.events import ItemType
from slim_guard.harness.failures import context_compilation_failure
from slim_guard.harness.initialization import (
    InitializedTurn,
    TurnInitializationRequest,
    TurnInitializer,
)
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.loop import HarnessLoopResult, ResponsePipelineResult
from slim_guard.harness.safety import (
    DefaultInputSafetyPolicy,
    InputSafetyPolicy,
    OutputGuard,
    PermissiveOutputGuard,
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.recall import MemoryRecaller, MemoryRecallResult
from slim_guard.observability.tracing import current_trace_id
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
    ResponseFinalizer,
)
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationStore
from slim_guard.tools.policy import ToolAuthorization


class TurnGrants(BaseModel):
    """Trusted per-run grants; the model never supplies this object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tool_names: tuple[str, ...] | None = None
    confirmed_execution_keys: frozenset[str] = frozenset()
    reviewed_execution_keys: frozenset[str] = frozenset()
    isolated_write_environment: bool = False


@dataclass(frozen=True, slots=True)
class TurnRunResult:
    initialized: InitializedTurn
    compiled: CompiledContext | None
    loop: HarnessLoopResult
    memory_recall: MemoryRecallResult | None = None
    response_finalization: ResponseFinalizationResult | None = None

    @property
    def final_text(self) -> str | None:
        return self.loop.final_text


class TurnHarness:
    """Application-level entry point for one new durable Agent Turn."""

    def __init__(
        self,
        *,
        initializer: TurnInitializer,
        compiler: ContextCompiler,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        recorder: HarnessRunRecorder,
        limits: HarnessLimits,
        context_data: ContextDataProvider | None = None,
        memory_recaller: MemoryRecaller | None = None,
        input_safety: InputSafetyPolicy | None = None,
        output_guard: OutputGuard | None = None,
        response_finalizer: ResponseFinalizer | None = None,
        invocation_store: InvocationStore | None = None,
        graph_version: str = "core-primary-v1",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._initializer = initializer
        self._compiler = compiler
        self._recorder = recorder
        self._context_data = context_data or EmptyContextDataProvider()
        self._memory_recaller = memory_recaller
        self._input_safety = input_safety or DefaultInputSafetyPolicy()
        self._response_finalizer = response_finalizer
        self._invocation_store = invocation_store
        self._graph_version = graph_version
        self._limits = limits
        self._output_guard = output_guard or PermissiveOutputGuard()
        self._clock = clock or self._utc_now
        self._core_agent = CoreAgent(
            model=model,
            tool_calls=tool_calls,
            recorder=recorder,
            limits=limits,
            output_guard=output_guard,
            clock=self._clock,
        )

    async def run(
        self,
        *,
        request: TurnInitializationRequest,
        grants: TurnGrants | None = None,
    ) -> TurnRunResult:
        current_time = self._clock()
        if current_time.utcoffset() is None:
            raise ValueError("Harness Turn Runner clock must be timezone-aware")
        initialized = await self._initializer.initialize(request)
        active_grants = grants or TurnGrants()
        safety_assessment = self._input_safety.assess(initialized.input_items)
        recall_result: MemoryRecallResult | None = None
        try:
            authoritative_context = dict(
                await self._context_data.load(
                    user_id=initialized.context.user_id,
                    current_time=current_time,
                    trigger=initialized.turn.trigger,
                    input_items=initialized.input_items,
                )
            )
            if self._memory_recaller is not None:
                recall_result = await self._memory_recaller.recall(
                    initialized=initialized,
                    current_time=current_time,
                    context=authoritative_context,
                )
                authoritative_context = recall_result.context
            if safety_assessment.code != "none":
                authoritative_context["health_safety"] = safety_assessment.to_context()
            allowed_tool_names = (
                () if safety_assessment.blocks_tools else active_grants.allowed_tool_names
            )
            compiled = self._compiler.compile(
                initialized=initialized,
                current_time=current_time,
                allowed_tool_names=allowed_tool_names,
                authoritative_context=authoritative_context,
            )
        except (ContextCompilationError, ValueError, TypeError) as exc:
            compilation_error = (
                exc
                if isinstance(exc, ContextCompilationError)
                else ContextCompilationError("Trusted context data is invalid")
            )
            failure = context_compilation_failure(compilation_error)
            await self._recorder.finish_run(
                turn_id=initialized.turn.id,
                termination=HarnessTermination.FATAL_ERROR,
                final_text=None,
                model_call_count=0,
                tool_call_count=0,
                total_token_count=0,
                failure=failure,
            )
            return TurnRunResult(
                initialized=initialized,
                compiled=None,
                loop=HarnessLoopResult(
                    termination=HarnessTermination.FATAL_ERROR,
                    final_text=None,
                    messages=(),
                    model_responses=(),
                    tool_outcomes=(),
                    failure=failure,
                ),
                memory_recall=recall_result,
            )

        authorization = ToolAuthorization(
            allowed_tool_names=frozenset(compiled.allowed_tool_names),
            confirmed_execution_keys=active_grants.confirmed_execution_keys,
            reviewed_execution_keys=active_grants.reviewed_execution_keys,
            isolated_write_environment=active_grants.isolated_write_environment,
        )
        await self._recorder.record_context_snapshot(
            turn_id=initialized.turn.id,
            payload={
                "compiled_at": current_time.isoformat(),
                "request": compiled.request.model_dump(mode="json"),
                "allowed_tool_names": list(compiled.allowed_tool_names),
                "input_item_ids": list(compiled.input_item_ids),
                "authorization": {
                    "allowed_tool_names": sorted(authorization.allowed_tool_names),
                    "confirmed_execution_keys": sorted(authorization.confirmed_execution_keys),
                    "reviewed_execution_keys": sorted(authorization.reviewed_execution_keys),
                    "isolated_write_environment": (authorization.isolated_write_environment),
                },
            },
        )
        finalization_result: ResponseFinalizationResult | None = None

        async def run_response_pipeline(
            neutral_draft: str,
            messages: tuple[ModelMessage, ...],
            outcomes: tuple[ToolCallOutcome, ...],
            responses: tuple[ModelResponse, ...],
        ) -> ResponsePipelineResult:
            nonlocal finalization_result
            if self._response_finalizer is None:
                return ResponsePipelineResult(text=neutral_draft)
            finalization_result = await self._response_finalizer.finalize(
                ResponseFinalizationRequest(
                    trace_id=current_trace_id() or initialized.turn.id,
                    user_id=initialized.context.user_id,
                    thread_id=initialized.thread.id,
                    turn_id=initialized.turn.id,
                    core_invocation_id=core_invocation.invocation_id,
                    neutral_draft=neutral_draft,
                    messages=messages,
                    tool_outcomes=outcomes,
                    model_responses=responses,
                    deadline_at=core_invocation.deadline_at,
                )
            )
            checked = self._output_guard.review(
                text=finalization_result.text,
                assessment=safety_assessment,
                tool_outcomes=outcomes,
            )
            if checked.modified:
                await self._recorder.record_workflow_event(
                    turn_id=initialized.turn.id,
                    event_type=ItemType.RESPONSE_DEGRADED,
                    payload={
                        "artifact_id": finalization_result.final_output_artifact_id,
                        "reason_code": "response_pipeline_output_guard_modified",
                        "fallback_type": "core_response",
                    },
                )
                return ResponsePipelineResult(
                    text=neutral_draft,
                    model_call_count=finalization_result.model_call_count,
                    total_token_count=finalization_result.total_token_count,
                    core_output_artifact_id=finalization_result.core_output_artifact_id,
                )
            await self._recorder.record_workflow_event(
                turn_id=initialized.turn.id,
                event_type=ItemType.RESPONSE_ADOPTED,
                payload={
                    "artifact_id": finalization_result.final_output_artifact_id,
                    "mode": "core_primary",
                    "final": True,
                },
            )
            return ResponsePipelineResult(
                text=checked.text,
                model_call_count=finalization_result.model_call_count,
                total_token_count=finalization_result.total_token_count,
                core_output_artifact_id=finalization_result.core_output_artifact_id,
                final_output_artifact_id=finalization_result.final_output_artifact_id,
            )

        core_invocation = AgentInvocation(
            invocation_id=f"inv-{uuid4()}",
            trace_id=current_trace_id() or initialized.turn.id,
            thread_id=initialized.thread.id,
            turn_id=initialized.turn.id,
            graph_version=self._graph_version,
            agent_role=AgentRole.CORE,
            agent_version=initialized.turn.agent_version_id,
            caller="turn_harness",
            input_schema="CompiledContext",
            allowed_tools=tuple(compiled.allowed_tool_names),
            privacy_scopes=("current_user_input", "working_memory", "profile"),
            deadline_at=(initialized.turn.deadline_at or current_time + timedelta(seconds=120)),
            max_model_calls=self._limits.max_model_calls,
            max_tool_calls=self._limits.max_tool_calls,
            max_total_tokens=self._limits.max_total_tokens,
            payload={
                "input_item_ids": list(compiled.input_item_ids),
                "context_evidence_ids": list(compiled.evidence_item_ids),
            },
        )
        await self._start_core_invocation(core_invocation, current_time)
        loop_result = await self._core_agent.run(
            request=compiled.request,
            context=replace(
                initialized.context,
                agent_invocation_id=core_invocation.invocation_id,
            ),
            authorization=authorization,
            source_item_id=initialized.source_item_id,
            now=current_time,
            trusted_evidence_item_ids=compiled.evidence_item_ids,
            safety_assessment=safety_assessment,
            response_pipeline_hook=(
                run_response_pipeline if self._response_finalizer is not None else None
            ),
            before_finish_hook=lambda result: self._complete_core_invocation(
                core_invocation,
                result,
            ),
        )
        return TurnRunResult(
            initialized=initialized,
            compiled=compiled,
            loop=loop_result,
            memory_recall=recall_result,
            response_finalization=finalization_result,
        )

    @staticmethod
    def _user_request(initialized: InitializedTurn) -> str:
        texts = [
            str(item.payload["text"]).strip()
            for item in initialized.input_items
            if item.item_type.value == "user_message"
            and isinstance(item.payload.get("text"), str)
            and str(item.payload["text"]).strip()
        ]
        return "\n".join(texts) or f"定期任务：{initialized.turn.trigger.value}"

    async def _start_core_invocation(
        self,
        invocation: AgentInvocation,
        started_at: datetime,
    ) -> None:
        if self._invocation_store is not None:
            await self._invocation_store.start_invocation(
                invocation,
                reason_summary="理解本轮任务并调用获准的业务或专业能力",
                started_at=started_at,
            )

    async def _complete_core_invocation(
        self,
        invocation: AgentInvocation,
        result: HarnessLoopResult,
    ) -> None:
        artifact: AgentArtifact | None = None
        artifact_id = result.core_output_artifact_id
        if result.final_text is not None and artifact_id is None:
            artifact = AgentArtifact.create(
                artifact_id=f"artifact-{uuid4()}",
                turn_id=invocation.turn_id,
                producer_role=ArtifactProducerRole.CORE,
                artifact_type="core_response",
                schema_version="1",
                payload={"text": result.final_text},
                created_at=self._clock(),
            )
            if self._invocation_store is not None:
                await self._invocation_store.append_artifact(
                    artifact,
                    invocation_id=invocation.invocation_id,
                )
            artifact_id = artifact.artifact_id
        status = (
            InvocationStatus.SUCCEEDED
            if result.termination is HarnessTermination.FINAL_RESPONSE
            else InvocationStatus.FAILED
            if result.termination
            in {
                HarnessTermination.FATAL_ERROR,
                HarnessTermination.DEADLINE_EXCEEDED,
                HarnessTermination.MAX_MODEL_CALLS,
                HarnessTermination.MAX_TOOL_CALLS,
                HarnessTermination.MAX_TOTAL_TOKENS,
            }
            else InvocationStatus.DEGRADED
        )
        failure_code = (
            result.failure.code
            if result.failure is not None
            else result.termination.value
            if status is InvocationStatus.FAILED
            else None
        )
        invocation_result = AgentResult(
            invocation_id=invocation.invocation_id,
            status=status,
            output_schema=(
                "ResponsePlan" if result.core_output_artifact_id is not None else "CoreResponse"
            ),
            output_schema_version="1",
            artifact_id=artifact_id,
            model_call_count=len(result.model_responses),
            tool_call_count=len(result.tool_outcomes),
            token_usage=sum(item.usage.total_tokens for item in result.model_responses),
            failure_code=failure_code,
        )
        if self._invocation_store is not None:
            await self._invocation_store.complete_invocation(
                invocation_result,
                completed_at=self._clock(),
            )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
