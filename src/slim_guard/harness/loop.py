from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from slim_guard.agent_models.errors import ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolChoice,
)
from slim_guard.group_chat.contracts import ChatMessage
from slim_guard.harness.events import TurnStatus
from slim_guard.harness.failures import HarnessFailure, model_gateway_failure
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder, NullHarnessRunRecorder
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode
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
    pipeline_model_call_count: int = 0
    pipeline_total_token_count: int = 0
    core_output_artifact_id: str | None = None
    final_output_artifact_id: str | None = None
    outgoing_messages: tuple[ChatMessage, ...] = ()

    @property
    def model_call_count(self) -> int:
        return len(self.model_responses) + self.pipeline_model_call_count

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_outcomes)

    @property
    def total_token_count(self) -> int:
        return (
            sum(response.usage.total_tokens for response in self.model_responses)
            + self.pipeline_total_token_count
        )


@dataclass(slots=True)
class LoopProgress:
    """Per-execution progress retained if an exception or cancellation interrupts Core."""

    messages: list[ModelMessage] = field(default_factory=list)
    model_responses: list[ModelResponse] = field(default_factory=list)
    tool_outcomes: list[ToolCallOutcome] = field(default_factory=list)

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
    """Runs one bounded model-tool-observation loop for an existing Turn."""

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
        nonretryable_failures: dict[tuple[str, str], int] = {}
        force_text_response = False

        while True:
            if self._deadline_exceeded(context):
                return self._result(
                    termination=HarnessTermination.DEADLINE_EXCEEDED,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            request_update: dict[str, object] = {"messages": tuple(messages)}
            if force_text_response:
                # Give the model one correction attempt, then remove executable
                # capabilities after the same non-retryable failure happens twice.
                # The final explanation still comes from the model.
                request_update.update({"tools": (), "tool_choice": ToolChoice.NONE})
            current_request = request.model_copy(update=request_update)
            try:
                model_started_at = self._clock()
                response = await self._model.complete(current_request)
                model_completed_at = self._clock()
            except ModelGatewayError as exc:
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
                return self._result(
                    termination=HarnessTermination.DEADLINE_EXCEEDED,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            calls = response.message.tool_calls
            if not calls:
                text = response.message.content
                if text is None or not text.strip():
                    raise ValueError("Model returned neither tool calls nor final text")
                return self._result(
                    termination=HarnessTermination.FINAL_RESPONSE,
                    final_text=text,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            for call in calls:
                if self._deadline_exceeded(context):
                    return self._result(
                        termination=HarnessTermination.DEADLINE_EXCEEDED,
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                trace = await self._recorder.start_tool_call(
                    turn_id=context.turn_id,
                    call=call,
                    call_index=len(tool_outcomes) + 1,
                )
                tool_started_at = self._clock()
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
                tool_completed_at = self._clock()
                tool_outcomes.append(outcome)
                await self._recorder.finish_tool_call(
                    trace=trace,
                    outcome=outcome,
                    started_at=tool_started_at,
                    completed_at=tool_completed_at,
                )
                messages.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=call.id,
                        content=outcome.execution.result.to_model_content(),
                    )
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
                if failure is not None and not failure.retryable:
                    failure_key = (outcome.execution.tool_name, failure.code)
                    failure_count = nonretryable_failures.get(failure_key, 0) + 1
                    nonretryable_failures[failure_key] = failure_count
                    # A non-retryable tool failure is already a structured
                    # decision from the tool. Give the model one text-only
                    # turn to explain it, but never let it issue the same
                    # write again with guessed arguments.
                    force_text_response = True
                    logger.warning(
                        "slim_guard_tool_failure_circuit_opened",
                        extra={
                            "turn_id": context.turn_id,
                            "tool_name": outcome.execution.tool_name,
                            "failure_code": failure.code,
                            "failure_count": failure_count,
                        },
                    )
                waiting = self._waiting_termination(outcome)
                if waiting is not None:
                    return self._result(
                        termination=waiting,
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                if self._deadline_exceeded(context):
                    return self._result(
                        termination=HarnessTermination.DEADLINE_EXCEEDED,
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                if failure is not None and not failure.retryable:
                    break

    @staticmethod
    def _waiting_termination(outcome: ToolCallOutcome) -> HarnessTermination | None:
        if outcome.turn.status is TurnStatus.WAITING_USER_CONFIRMATION:
            return HarnessTermination.WAITING_USER_CONFIRMATION
        if outcome.turn.status is TurnStatus.WAITING_HUMAN_REVIEW:
            return HarnessTermination.WAITING_HUMAN_REVIEW
        return None

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

    def _deadline_exceeded(self, context: HarnessTurnContext) -> bool:
        if context.deadline_at is None:
            return False
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Harness clock must return a timezone-aware datetime")
        return now >= context.deadline_at

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
