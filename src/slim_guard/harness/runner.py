from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from slim_guard.agent_models.gateway import ModelGateway, ModelMessage, ModelResponse
from slim_guard.agents.contracts import InvocationStatus
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
from slim_guard.harness.loop import FinalResponseCandidate, HarnessLoop, HarnessLoopResult
from slim_guard.harness.safety import (
    DefaultInputSafetyPolicy,
    InputSafetyPolicy,
    OutputGuard,
    PermissiveOutputGuard,
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.ingestion import MemoryIngestionResult, MemoryIngestor
from slim_guard.memory.recall import MemoryRecaller, MemoryRecallResult
from slim_guard.observability.tracing import current_trace_id
from slim_guard.orchestration.coordinator import (
    AgentWorkflowCoordinator,
    ShadowWorkflowRequest,
    ShadowWorkflowResult,
)
from slim_guard.tools.policy import ToolAuthorization


class HarnessTurnGrants(BaseModel):
    """Trusted per-run grants; the model never supplies this object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tool_names: tuple[str, ...] | None = None
    confirmed_execution_keys: frozenset[str] = frozenset()
    reviewed_execution_keys: frozenset[str] = frozenset()
    isolated_write_environment: bool = False


@dataclass(frozen=True, slots=True)
class HarnessTurnRunResult:
    initialized: InitializedTurn
    compiled: CompiledContext | None
    loop: HarnessLoopResult
    memory_ingestion: MemoryIngestionResult | None = None
    memory_recall: MemoryRecallResult | None = None
    shadow_workflow: ShadowWorkflowResult | None = None

    @property
    def final_text(self) -> str | None:
        return self.loop.final_text


class HarnessTurnRunner:
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
        memory_ingestor: MemoryIngestor | None = None,
        memory_recaller: MemoryRecaller | None = None,
        input_safety: InputSafetyPolicy | None = None,
        output_guard: OutputGuard | None = None,
        shadow_workflow: AgentWorkflowCoordinator | None = None,
        shadow_enabled_for: Callable[[str], bool] | None = None,
        workflow_mode: Literal["off", "shadow", "canary", "on"] = "off",
        workflow_adopts_for: Callable[[str], bool] | None = None,
        workflow_timeout_seconds: float = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._initializer = initializer
        self._compiler = compiler
        self._recorder = recorder
        self._context_data = context_data or EmptyContextDataProvider()
        self._memory_ingestor = memory_ingestor
        self._memory_recaller = memory_recaller
        self._input_safety = input_safety or DefaultInputSafetyPolicy()
        self._shadow_workflow = shadow_workflow
        self._shadow_enabled_for = shadow_enabled_for or (lambda _user_id: False)
        self._workflow_mode = workflow_mode
        self._workflow_adopts_for = workflow_adopts_for or (lambda _user_id: False)
        self._workflow_timeout_seconds = workflow_timeout_seconds
        self._limits = limits
        self._output_guard = output_guard or PermissiveOutputGuard()
        self._clock = clock or self._utc_now
        self._loop = HarnessLoop(
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
        grants: HarnessTurnGrants | None = None,
    ) -> HarnessTurnRunResult:
        current_time = self._clock()
        if current_time.utcoffset() is None:
            raise ValueError("Harness Turn Runner clock must be timezone-aware")
        initialized = await self._initializer.initialize(request)
        active_grants = grants or HarnessTurnGrants()
        safety_assessment = self._input_safety.assess(initialized.input_items)
        ingestion_result: MemoryIngestionResult | None = None
        recall_result: MemoryRecallResult | None = None
        try:
            if self._memory_ingestor is not None and not safety_assessment.blocks_tools:
                ingestion_result = await self._memory_ingestor.ingest(
                    initialized=initialized,
                    current_time=current_time,
                    isolated_write_environment=active_grants.isolated_write_environment,
                )
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
            if ingestion_result is not None:
                memory_receipt = ingestion_result.context_receipt()
                if memory_receipt is not None:
                    authoritative_context["current_turn_memory_receipt"] = memory_receipt
            if safety_assessment.blocks_tools:
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
            return HarnessTurnRunResult(
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
                memory_ingestion=ingestion_result,
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
        shadow_result: ShadowWorkflowResult | None = None

        async def finalize_response(
            baseline: str,
            messages: tuple[ModelMessage, ...],
            outcomes: tuple[ToolCallOutcome, ...],
            responses: tuple[ModelResponse, ...],
        ) -> str:
            nonlocal shadow_result
            if self._shadow_workflow is None:
                return baseline
            timeout = self._workflow_timeout_seconds
            if initialized.turn.deadline_at is not None:
                timeout = min(
                    timeout, (initialized.turn.deadline_at - self._clock()).total_seconds()
                )
            if timeout <= 0:
                return baseline
            try:
                async with asyncio.timeout(timeout):
                    if self._workflow_mode == "shadow":
                        # Shadow runs after the baseline loop so that it can inspect this
                        # turn's real image-tool receipt. It retains its own configured
                        # budget because its output is never eligible for delivery.
                        remaining_calls = None
                        remaining_tokens = None
                    else:
                        remaining_calls = max(0, self._limits.max_model_calls - len(responses))
                        remaining_tokens = max(
                            0,
                            self._limits.max_total_tokens
                            - sum(response.usage.total_tokens for response in responses),
                        )
                    if remaining_calls == 0 or remaining_tokens == 0:
                        await self._recorder.record_workflow_event(
                            turn_id=initialized.turn.id,
                            event_type=ItemType.RESPONSE_DEGRADED,
                            payload={
                                "artifact_id": None,
                                "reason_code": "turn_workflow_budget_exhausted",
                                "fallback_type": "legacy_response",
                            },
                        )
                        return baseline
                    refreshed_context = dict(
                        await self._context_data.load(
                            user_id=initialized.context.user_id,
                            current_time=self._clock(),
                            trigger=initialized.turn.trigger,
                            input_items=initialized.input_items,
                        )
                    )
                    if "current_turn_memory_receipt" in authoritative_context:
                        refreshed_context["current_turn_memory_receipt"] = authoritative_context[
                            "current_turn_memory_receipt"
                        ]
                    fresh_compiled = self._compiler.compile(
                        initialized=initialized,
                        current_time=self._clock(),
                        allowed_tool_names=allowed_tool_names,
                        authoritative_context=refreshed_context,
                    )
                    # Replace the stale context prefix, retaining only this turn's
                    # actual tool exchanges and guarded baseline response.
                    fresh_messages = (
                        *fresh_compiled.request.messages,
                        *messages[len(compiled.request.messages) :],
                    )
                    receipts = tuple(
                        {
                            "id": "tool-call:" + outcome.execution.tool_call_id,
                            "item_type": "tool_result",
                            "payload": {
                                "tool_name": outcome.execution.tool_name,
                                "tool_version": outcome.execution.tool_version,
                                **outcome.execution.result.model_dump(mode="json"),
                            },
                        }
                        for outcome in outcomes
                    )
                    shadow_result = await self._shadow_workflow.run_shadow(
                        ShadowWorkflowRequest(
                            user_id=initialized.context.user_id,
                            trace_id=current_trace_id() or initialized.turn.id,
                            turn_id=initialized.turn.id,
                            thread_id=initialized.thread.id,
                            context=fresh_messages[-64:],
                            user_request=self._user_request(initialized),
                            current_items=tuple(
                                {
                                    "id": item.id,
                                    "item_type": item.item_type.value,
                                    "payload": item.payload,
                                }
                                for item in initialized.input_items
                            )
                            + receipts,
                            authoritative_context=refreshed_context,
                            legacy_response=baseline,
                            mode=self._workflow_mode,
                            max_model_calls=remaining_calls,
                            max_total_tokens=remaining_tokens,
                            deadline_at=initialized.turn.deadline_at,
                        )
                    )
            except Exception:
                await self._recorder.record_workflow_event(
                    turn_id=initialized.turn.id,
                    event_type=ItemType.RESPONSE_DEGRADED,
                    payload={
                        "artifact_id": None,
                        "reason_code": "workflow_timeout_or_error",
                        "fallback_type": "legacy_response",
                    },
                )
                return baseline
            if self._workflow_mode == "shadow":
                return baseline
            candidate = shadow_result.shadow_candidate
            selected = next(
                (
                    item
                    for item in reversed(shadow_result.artifacts)
                    if item.artifact_type in {"styled_response", "neutral_response"}
                ),
                None,
            )
            latest_review = next(
                (
                    item
                    for item in reversed(shadow_result.artifacts)
                    if item.artifact_type == "reviewer_verdict"
                ),
                None,
            )
            eligible = (
                shadow_result.status is InvocationStatus.SUCCEEDED
                and candidate is not None
                and latest_review is not None
                and latest_review.payload.get("verdict") == "pass"
                and selected is not None
                and selected.turn_id == latest_review.turn_id == initialized.turn.id
                and selected.verify_payload()
                and latest_review.verify_payload()
                and selected.payload.get("text") == candidate
                and selected.artifact_id in latest_review.parent_artifact_ids
                and selected.artifact_id in latest_review.payload.get("reviewed_artifact_ids", [])
            )
            if eligible and candidate is not None:
                checked = self._output_guard.review(
                    text=candidate,
                    assessment=safety_assessment,
                    tool_outcomes=outcomes,
                )
                eligible = not checked.modified
            if not eligible:
                await self._recorder.record_workflow_event(
                    turn_id=initialized.turn.id,
                    event_type=ItemType.RESPONSE_DEGRADED,
                    payload={
                        "artifact_id": None,
                        "reason_code": shadow_result.failure_code or "candidate_not_adoptable",
                        "fallback_type": "legacy_response",
                    },
                )
                return baseline
            assert selected is not None
            await self._recorder.record_workflow_event(
                turn_id=initialized.turn.id,
                event_type=ItemType.RESPONSE_ADOPTED,
                payload={
                    "artifact_id": selected.artifact_id,
                    "mode": self._workflow_mode,
                    "final": True,
                },
            )
            # Adoption is not channel delivery; delivery is owned by the outbox.
            assert candidate is not None
            return candidate

        async def finalize_with_usage(
            baseline: str,
            messages: tuple[ModelMessage, ...],
            outcomes: tuple[ToolCallOutcome, ...],
            responses: tuple[ModelResponse, ...],
        ) -> FinalResponseCandidate:
            text = await finalize_response(baseline, messages, outcomes, responses)
            return FinalResponseCandidate(
                text=text,
                model_call_count=shadow_result.model_call_count if shadow_result else 0,
                total_token_count=shadow_result.total_token_count if shadow_result else 0,
            )

        adopt = (
            self._workflow_mode in {"canary", "on"}
            and self._workflow_adopts_for(initialized.context.user_id)
            and not safety_assessment.blocks_tools
        )
        evaluate_shadow = (
            self._workflow_mode == "shadow"
            and self._shadow_workflow is not None
            and self._shadow_enabled_for(initialized.context.user_id)
            and not safety_assessment.blocks_tools
        )
        loop_result = await self._loop.run(
            request=compiled.request,
            context=initialized.context,
            authorization=authorization,
            source_item_id=initialized.source_item_id,
            now=current_time,
            trusted_evidence_item_ids=compiled.evidence_item_ids,
            safety_assessment=safety_assessment,
            final_response_hook=finalize_with_usage if adopt or evaluate_shadow else None,
        )
        if shadow_result is not None:
            shadow_result = replace(
                shadow_result,
                legacy_response=(shadow_result.legacy_response or loop_result.final_text),
            )
        return HarnessTurnRunResult(
            initialized=initialized,
            compiled=compiled,
            loop=loop_result,
            memory_ingestion=ingestion_result,
            memory_recall=recall_result,
            shadow_workflow=shadow_result,
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

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
