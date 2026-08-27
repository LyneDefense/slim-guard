from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from slim_guard.agent_models.errors import ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from slim_guard.harness.events import TurnStatus
from slim_guard.harness.failures import HarnessFailure, model_gateway_failure
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder, NullHarnessRunRecorder
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode
from slim_guard.tools.policy import ToolAuthorization


@dataclass(frozen=True, slots=True)
class HarnessTurnContext:
    thread_id: str
    turn_id: str
    user_id: str
    agent_version_id: str
    execution_mode: ToolExecutionMode
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.deadline_at is not None and self.deadline_at.utcoffset() is None:
            raise ValueError("Harness Turn deadline must be timezone-aware")

    def for_tool_call(self, tool_call_id: str) -> ToolContext:
        return ToolContext(
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            tool_call_id=tool_call_id,
            user_id=self.user_id,
            agent_version_id=self.agent_version_id,
            execution_mode=self.execution_mode,
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


class HarnessLoop:
    """Runs one bounded model-tool-observation loop for an existing Turn."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        limits: HarnessLimits,
        recorder: HarnessRunRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._tool_calls = tool_calls
        self._limits = limits
        self._recorder = recorder or NullHarnessRunRecorder()
        self._clock = clock or self._utc_now

    async def run(
        self,
        *,
        request: ModelRequest,
        context: HarnessTurnContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
    ) -> HarnessLoopResult:
        messages = list(request.messages)
        model_responses: list[ModelResponse] = []
        tool_outcomes: list[ToolCallOutcome] = []

        while True:
            if self._deadline_exceeded(context):
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.DEADLINE_EXCEEDED,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            if len(model_responses) >= self._limits.max_model_calls:
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.MAX_MODEL_CALLS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            current_request = request.model_copy(update={"messages": tuple(messages)})
            if self._total_tokens(model_responses) >= self._limits.max_total_tokens:
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.MAX_TOTAL_TOKENS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            try:
                response = await self._model.complete(current_request)
            except ModelGatewayError as exc:
                return await self._finish(
                    context=context,
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
            )
            if self._total_tokens(model_responses) > self._limits.max_total_tokens:
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.MAX_TOTAL_TOKENS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            if self._deadline_exceeded(context):
                return await self._finish(
                    context=context,
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
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.FINAL_RESPONSE,
                    final_text=text.strip(),
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            if len(tool_outcomes) + len(calls) > self._limits.max_tool_calls:
                return await self._finish(
                    context=context,
                    termination=HarnessTermination.MAX_TOOL_CALLS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            for call in calls:
                if self._deadline_exceeded(context):
                    return await self._finish(
                        context=context,
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
                outcome = await self._tool_calls.execute(
                    call=call,
                    context=context.for_tool_call(call.id),
                    authorization=authorization,
                    source_item_id=source_item_id,
                    now=now,
                )
                tool_outcomes.append(outcome)
                await self._recorder.finish_tool_call(trace=trace, outcome=outcome)
                messages.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=call.id,
                        content=outcome.execution.result.to_model_content(),
                    )
                )
                waiting = self._waiting_termination(outcome)
                if waiting is not None:
                    return await self._finish(
                        context=context,
                        termination=waiting,
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )
                if self._deadline_exceeded(context):
                    return await self._finish(
                        context=context,
                        termination=HarnessTermination.DEADLINE_EXCEEDED,
                        messages=messages,
                        model_responses=model_responses,
                        tool_outcomes=tool_outcomes,
                    )

    @staticmethod
    def _waiting_termination(outcome: ToolCallOutcome) -> HarnessTermination | None:
        if outcome.turn.status is TurnStatus.WAITING_USER_CONFIRMATION:
            return HarnessTermination.WAITING_USER_CONFIRMATION
        if outcome.turn.status is TurnStatus.WAITING_HUMAN_REVIEW:
            return HarnessTermination.WAITING_HUMAN_REVIEW
        return None

    async def _finish(
        self,
        *,
        context: HarnessTurnContext,
        termination: HarnessTermination,
        messages: list[ModelMessage],
        model_responses: list[ModelResponse],
        tool_outcomes: list[ToolCallOutcome],
        final_text: str | None = None,
        failure: HarnessFailure | None = None,
    ) -> HarnessLoopResult:
        await self._recorder.finish_run(
            turn_id=context.turn_id,
            termination=termination,
            final_text=final_text,
            model_call_count=len(model_responses),
            tool_call_count=len(tool_outcomes),
            total_token_count=self._total_tokens(model_responses),
            failure=failure,
        )
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
    def _total_tokens(model_responses: list[ModelResponse]) -> int:
        return sum(response.usage.total_tokens for response in model_responses)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
