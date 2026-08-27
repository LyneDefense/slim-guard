from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from slim_guard.harness.events import TurnStatus
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome, ToolCallRunner
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode
from slim_guard.tools.policy import ToolAuthorization


@dataclass(frozen=True, slots=True)
class HarnessTurnContext:
    thread_id: str
    turn_id: str
    user_id: str
    agent_version_id: str
    execution_mode: ToolExecutionMode

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

    @property
    def model_call_count(self) -> int:
        return len(self.model_responses)

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_outcomes)


class HarnessLoop:
    """Runs one bounded model-tool-observation loop for an existing Turn."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        limits: HarnessLimits,
    ) -> None:
        self._model = model
        self._tool_calls = tool_calls
        self._limits = limits

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
            if len(model_responses) >= self._limits.max_model_calls:
                return self._result(
                    termination=HarnessTermination.MAX_MODEL_CALLS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )
            response = await self._model.complete(
                request.model_copy(update={"messages": tuple(messages)})
            )
            model_responses.append(response)
            messages.append(response.message)

            calls = response.message.tool_calls
            if not calls:
                text = response.message.content
                if text is None or not text.strip():
                    raise ValueError("Model returned neither tool calls nor final text")
                return self._result(
                    termination=HarnessTermination.FINAL_RESPONSE,
                    final_text=text.strip(),
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            if len(tool_outcomes) + len(calls) > self._limits.max_tool_calls:
                return self._result(
                    termination=HarnessTermination.MAX_TOOL_CALLS,
                    messages=messages,
                    model_responses=model_responses,
                    tool_outcomes=tool_outcomes,
                )

            for call in calls:
                outcome = await self._tool_calls.execute(
                    call=call,
                    context=context.for_tool_call(call.id),
                    authorization=authorization,
                    source_item_id=source_item_id,
                    now=now,
                )
                tool_outcomes.append(outcome)
                messages.append(
                    ModelMessage(
                        role=MessageRole.TOOL,
                        tool_call_id=call.id,
                        content=outcome.execution.result.to_model_content(),
                    )
                )
                waiting = self._waiting_termination(outcome)
                if waiting is not None:
                    return self._result(
                        termination=waiting,
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

    @staticmethod
    def _result(
        *,
        termination: HarnessTermination,
        messages: list[ModelMessage],
        model_responses: list[ModelResponse],
        tool_outcomes: list[ToolCallOutcome],
        final_text: str | None = None,
    ) -> HarnessLoopResult:
        return HarnessLoopResult(
            termination=termination,
            final_text=final_text,
            messages=tuple(messages),
            model_responses=tuple(model_responses),
            tool_outcomes=tuple(tool_outcomes),
        )
