from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from slim_guard.agent_models.errors import InvalidModelResponse, ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.harness.failures import HarnessFailure, model_gateway_failure
from slim_guard.harness.model_response_recovery import (
    MAX_FORMAT_REPAIR_ATTEMPTS,
    expand_repair_request,
    repair_feedback,
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder, NullHarnessRunRecorder
from slim_guard.tools.contracts import (
    ToolContext,
    ToolEffectStatus,
    ToolExecutionMode,
    ToolResult,
)
from slim_guard.tools.policy import ToolAuthorization
from slim_guard.usage.context import usage_operation

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HarnessTurnContext:
    thread_id: str
    turn_id: str
    user_id: str
    agent_version_id: str
    execution_mode: ToolExecutionMode
    deadline_at: datetime | None = None
    agent_invocation_id: str | None = None

    def __post_init__(self) -> None:
        if self.deadline_at is not None and self.deadline_at.utcoffset() is None:
            raise ValueError("Harness Turn deadline must be timezone-aware")

    def for_tool_call(
        self,
        tool_call_id: str,
        *,
        source_item_id: str | None = None,
        trusted_evidence_item_ids: tuple[str, ...] = (),
    ) -> ToolContext:
        return ToolContext(
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            tool_call_id=tool_call_id,
            user_id=self.user_id,
            agent_version_id=self.agent_version_id,
            agent_invocation_id=self.agent_invocation_id,
            execution_mode=self.execution_mode,
            source_item_id=source_item_id,
            trusted_evidence_item_ids=trusted_evidence_item_ids,
        )


@dataclass(frozen=True, slots=True)
class HarnessLoopResult:
    termination: HarnessTermination
    final_text: str | None
    messages: tuple[ModelMessage, ...]
    model_responses: tuple[ModelResponse, ...]
    tool_outcomes: tuple[ToolCallOutcome, ...]
    failure: HarnessFailure | None = None

    @property
    def model_call_count(self) -> int:
        return len(self.model_responses)

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_outcomes)

    @property
    def total_token_count(self) -> int:
        return sum(response.usage.total_tokens for response in self.model_responses)


@dataclass(slots=True)
class LoopProgress:
    """Per-execution progress retained if an exception or cancellation interrupts Core."""

    messages: list[ModelMessage] = field(default_factory=list)
    model_responses: list[ModelResponse] = field(default_factory=list)
    tool_outcomes: list[ToolCallOutcome] = field(default_factory=list)
    model_request_attempts: int = 0
    format_repair_attempts: int = 0
    successful_tool_signatures: set[str] = field(default_factory=set)
    rejected_tool_signatures: dict[str, str] = field(default_factory=dict)

    def interrupted(self, error: BaseException, *, cancelled: bool = False) -> HarnessLoopResult:
        return HarnessLoopResult(
            termination=HarnessTermination.FATAL_ERROR,
            final_text=None,
            messages=tuple(self.messages),
            model_responses=tuple(self.model_responses),
            tool_outcomes=tuple(self.tool_outcomes),
            failure=HarnessFailure(
                code="execution_cancelled" if cancelled else "core_execution_failed",
                error_type=type(error).__name__,
                retryable=cancelled,
            ),
        )


class HarnessLoop:
    """Runs the model-tool-observation loop for an existing Turn."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        recorder: HarnessRunRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._tool_calls = tool_calls
        self._recorder = recorder or NullHarnessRunRecorder()
        self._clock = clock or self._utc_now

    @usage_operation("core", source="context", kind="agent", agent="core")
    async def run(
        self,
        *,
        request: ModelRequest,
        context: HarnessTurnContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
        trusted_evidence_item_ids: tuple[str, ...] = (),
        progress: LoopProgress | None = None,
    ) -> HarnessLoopResult:
        state = progress if progress is not None else LoopProgress()
        state.messages.extend(request.messages)
        messages = state.messages
        model_responses = state.model_responses
        tool_outcomes = state.tool_outcomes

        while True:
            if self._deadline_exceeded(context):
                return self._deadline_result(
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            request_update: dict[str, object] = {"messages": tuple(messages)}
            current_request = request.model_copy(update=request_update)
            state.model_request_attempts += 1
            model_started_at = self._clock()
            try:
                response = await self._model.complete(current_request)
                model_completed_at = self._clock()
            except ModelGatewayError as exc:
                if isinstance(exc, InvalidModelResponse):
                    repair_attempted = (
                        exc.repairable and state.format_repair_attempts < MAX_FORMAT_REPAIR_ATTEMPTS
                    )
                    await self._recorder.record_invalid_model_response(
                        turn_id=context.turn_id,
                        request=current_request,
                        error=exc,
                        call_index=state.model_request_attempts,
                        failed_at=self._clock(),
                        repair_attempted=repair_attempted,
                    )
                if (
                    isinstance(exc, InvalidModelResponse)
                    and exc.repairable
                    and state.format_repair_attempts < MAX_FORMAT_REPAIR_ATTEMPTS
                ):
                    state.format_repair_attempts += 1
                    messages.append(repair_feedback(exc))
                    request = expand_repair_request(request, error=exc)
                    logger.warning(
                        "slim_guard_model_response_repair_requested",
                        extra={
                            "turn_id": context.turn_id,
                            "reason_code": exc.reason_code,
                            "field_path": exc.field_path,
                            "attempt": state.format_repair_attempts,
                            "output_limit_hit": exc.output_limit_hit,
                        },
                    )
                    await self._recorder.record_workflow_event(
                        turn_id=context.turn_id,
                        event_type="workflow_transition",
                        payload={
                            "from_node": "model_response_validation",
                            "to_node": "model_response_repair",
                            "transition_type": "repair",
                            "reason_code": exc.reason_code,
                            "attempt": state.format_repair_attempts,
                        },
                    )
                    continue
                return self._result(
                    termination=HarnessTermination.FATAL_ERROR,
                    failure=model_gateway_failure(exc),
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            model_responses.append(response)
            messages.append(response.message)
            await self._recorder.record_model_response(
                turn_id=context.turn_id,
                request=current_request,
                response=response,
                call_index=len(model_responses),
                started_at=model_started_at,
                completed_at=model_completed_at,
            )
            if self._deadline_exceeded(context):
                return self._deadline_result(
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            calls = response.message.tool_calls
            if not calls:
                text = response.message.content
                if text is None or not text.strip():
                    return self._result(
                        termination=HarnessTermination.FATAL_ERROR,
                        failure=HarnessFailure(
                            code="empty_model_response",
                            error_type="EmptyModelResponse",
                            retryable=False,
                        ),
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                return self._result(
                    termination=HarnessTermination.FINAL_RESPONSE,
                    final_text=text,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            # Tool dependencies are planned by Core. Each completed observation is
            # retained immediately; no approval gate keeps this Turn open.
            for call in calls:
                if self._deadline_exceeded(context):
                    return self._deadline_result(
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                signature = self._tool_call_signature(call)
                if signature in state.rejected_tool_signatures:
                    messages.append(
                        ModelMessage(
                            role=MessageRole.TOOL,
                            tool_call_id=call.id,
                            content=state.rejected_tool_signatures[signature],
                        )
                    )
                    await self._recorder.record_workflow_event(
                        turn_id=context.turn_id,
                        event_type="workflow_transition",
                        payload={
                            "tool_name": call.name,
                            "tool_call_id": call.id,
                            "reason_code": "duplicate_rejected_tool_suppressed",
                            "from_node": "tool_validation",
                            "to_node": "core",
                            "transition_type": "repair",
                            "attempt": state.model_request_attempts,
                        },
                    )
                    continue
                if state.format_repair_attempts and signature in state.successful_tool_signatures:
                    messages.append(
                        ModelMessage(
                            role=MessageRole.TOOL,
                            tool_call_id=call.id,
                            content=ToolResult.failed(
                                code="duplicate_successful_operation_suppressed",
                                message=(
                                    "This identical operation already succeeded earlier in this "
                                    "Turn and was not executed again. "
                                    "Continue using its prior result."
                                ),
                                retryable=False,
                                effect_status=ToolEffectStatus.NOT_APPLIED,
                            ).to_model_content(),
                        )
                    )
                    continue
                trace = await self._recorder.start_tool_call(
                    turn_id=context.turn_id,
                    call=call,
                    call_index=len(tool_outcomes) + 1,
                )
                started_at = self._clock()
                outcome = await self._tool_calls.execute(
                    call=call,
                    context=context.for_tool_call(
                        call.id,
                        source_item_id=source_item_id,
                        trusted_evidence_item_ids=trusted_evidence_item_ids,
                    ),
                    authorization=authorization,
                    source_item_id=source_item_id,
                    now=now,
                )
                completed_at = self._clock()
                tool_outcomes.append(outcome)
                if outcome.execution.result.failure is None:
                    state.successful_tool_signatures.add(signature)
                elif (
                    outcome.execution.result.failure.effect_status is ToolEffectStatus.NOT_APPLIED
                    and not outcome.execution.result.failure.retryable
                    and outcome.execution.result.failure.field_errors
                ):
                    previous = outcome.execution.result
                    assert previous.failure is not None
                    state.rejected_tool_signatures[signature] = previous.model_copy(
                        update={
                            "failure": previous.failure.model_copy(
                                update={
                                    "message": previous.failure.message
                                    + " Identical arguments already "
                                    "failed and were not executed again. "
                                    "Correct the indicated fields "
                                    "or report that the operation did not complete.",
                                }
                            ),
                        }
                    ).to_model_content()
                messages.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=call.id,
                        content=outcome.execution.result.to_model_content(),
                    )
                )
                await self._recorder.finish_tool_call(
                    trace=trace,
                    outcome=outcome,
                    started_at=started_at,
                    completed_at=completed_at,
                )
                failure = outcome.execution.result.failure
                if failure is not None:
                    logger.warning(
                        "slim_guard_tool_call_failed",
                        extra={
                            "turn_id": context.turn_id,
                            "tool_name": outcome.execution.tool_name,
                            "failure_code": failure.code,
                            "retryable": failure.retryable,
                            "tool_call_index": len(tool_outcomes),
                        },
                    )

    @staticmethod
    def _result(
        *,
        termination: HarnessTermination,
        messages: list[ModelMessage],
        model_responses: list[ModelResponse],
        tool_outcomes: list[ToolCallOutcome],
        final_text: str | None = None,
        failure: HarnessFailure | None = None,
    ) -> HarnessLoopResult:
        # Only the outer TurnHarness can persist the final Turn status/messages.
        return HarnessLoopResult(
            termination=termination,
            final_text=final_text,
            messages=tuple(messages),
            model_responses=tuple(model_responses),
            tool_outcomes=tuple(tool_outcomes),
            failure=failure,
        )

    @classmethod
    def _deadline_result(
        cls,
        *,
        messages: list[ModelMessage],
        model_responses: list[ModelResponse],
        tool_outcomes: list[ToolCallOutcome],
    ) -> HarnessLoopResult:
        return cls._result(
            termination=HarnessTermination.DEADLINE_EXCEEDED,
            failure=HarnessFailure(
                code="turn_deadline_exceeded",
                error_type="TurnDeadlineExceeded",
                retryable=False,
            ),
            messages=messages,
            model_responses=model_responses,
            tool_outcomes=tool_outcomes,
        )

    def _deadline_exceeded(self, context: HarnessTurnContext) -> bool:
        if context.deadline_at is None:
            return False
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Harness clock must return a timezone-aware datetime")
        return now >= context.deadline_at

    @staticmethod
    def _tool_call_signature(call: NormalizedToolCall) -> str:
        canonical_arguments = json.dumps(
            call.arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"{call.name}:{canonical_arguments}"

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
