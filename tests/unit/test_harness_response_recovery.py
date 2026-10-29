from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.errors import InvalidModelResponse
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.harness.events import TurnStatus, TurnTrigger
from slim_guard.harness.loop import HarnessLoop, HarnessTurnContext
from slim_guard.harness.state_repository import TurnRef
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.tools.contracts import (
    ToolContext,
    ToolEffectStatus,
    ToolExecution,
    ToolExecutionMode,
    ToolFieldError,
    ToolResult,
)
from slim_guard.tools.policy import ToolAuthorization


def tool_definition() -> ToolDefinition:
    return ToolDefinition(
        name="record_weight",
        description="Record the current weight.",
        parameters_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        version="v1",
    )


def request(*, with_tool: bool = False) -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="fake-model",
        messages=(
            ModelMessage(role=MessageRole.SYSTEM, content="Core instructions"),
            ModelMessage(role=MessageRole.USER, content="今天记录体重"),
        ),
        tools=(tool_definition(),) if with_tool else (),
        tool_choice=ToolChoice.AUTO if with_tool else ToolChoice.NONE,
        max_output_tokens=1024,
    )


def context() -> HarnessTurnContext:
    return HarnessTurnContext(
        thread_id="thread-1",
        turn_id="turn-1",
        user_id="user-1",
        agent_version_id="agent-v1",
        execution_mode=ToolExecutionMode.EVALUATION,
    )


def authorization() -> ToolAuthorization:
    return ToolAuthorization(
        allowed_tool_names=frozenset({"record_weight"}),
        isolated_write_environment=True,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


def call_response(call_id: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id=call_id,
                    name="record_weight",
                    arguments={"weight_kg": 77.6},
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


def test_invalid_output_excerpt_is_bounded() -> None:
    error = InvalidModelResponse("malformed", raw_output_excerpt="x" * 5000)

    assert len(error.raw_output_excerpt or "") == 4096
    assert error.raw_output_truncated is True


class NoToolCalls:
    async def execute(self, **_kwargs):
        raise AssertionError("No tool call should be executed")


class SuccessfulToolCall:
    def __init__(self) -> None:
        self.calls: list[NormalizedToolCall] = []

    def requires_waiting(self, **_kwargs) -> bool:
        return False

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
        **_kwargs,
    ) -> ToolCallOutcome:
        assert call.name in authorization.allowed_tool_names
        self.calls.append(call)
        return ToolCallOutcome(
            execution=ToolExecution(
                tool_call_id=call.id,
                tool_name=call.name,
                tool_version="v1",
                canonical_arguments=call.arguments,
                idempotency_key=f"operation-{call.id}",
                result=ToolResult.success(output={"recorded": True}),
            ),
            turn=TurnRef(
                id=context.turn_id,
                thread_id=context.thread_id,
                agent_version_id=context.agent_version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                status=TurnStatus.RUNNING,
                deadline_at=None,
                completed_at=None,
            ),
        )


@pytest.mark.parametrize("field_error, expected_calls", [(True, 2), (False, 3)])
async def test_only_field_rejections_are_cached_and_corrected_arguments_can_repair(
    field_error, expected_calls
):
    class RejectingTool(SuccessfulToolCall):
        async def execute(self, **kwargs):
            outcome = await super().execute(**kwargs)
            if kwargs["call"].arguments["weight_kg"] == 77.6:
                outcome = ToolCallOutcome(
                    execution=outcome.execution.model_copy(
                        update={
                            "result": ToolResult.failed(
                                code="invalid_value",
                                message="Correct weight_kg",
                                effect_status=ToolEffectStatus.NOT_APPLIED,
                                field_errors=(
                                    ToolFieldError(
                                        path=("weight_kg",),
                                        code="invalid_value",
                                        message="Correct value",
                                    ),
                                )
                                if field_error
                                else (),
                            ),
                        }
                    ),
                    turn=outcome.turn,
                )
            return outcome

    corrected = call_response("third").model_copy(
        update={
            "message": ModelMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id="third", name="record_weight", arguments={"weight_kg": 77.7}
                    ),
                ),
            )
        }
    )
    model = ScriptedModelGateway(
        [call_response("first"), call_response("second"), corrected, text_response("完成")]
    )
    tools = RejectingTool()
    result = await run(model, tools, model_request=request(with_tool=True))
    assert len(tools.calls) == expected_calls
    assert result.termination is HarnessTermination.FINAL_RESPONSE
    feedback = [item.content for item in result.messages if item.tool_call_id == "second"]
    assert ("Identical arguments already failed" in feedback[0]) is field_error


async def run(
    model,
    tool_runner,
    *,
    model_request: ModelRequest | None = None,
    recorder=None,
):
    return await HarnessLoop(
        model=model,
        tool_calls=tool_runner,
        recorder=recorder or NullHarnessRunRecorder(),
    ).run(
        request=model_request or request(),
        context=context(),
        authorization=authorization(),
        source_item_id="user-item-1",
        now=datetime.now(UTC),
    )


async def test_invalid_format_is_sent_back_once_with_sanitized_diagnostics():
    class CapturingRecorder(NullHarnessRunRecorder):
        def __init__(self) -> None:
            self.invalid_responses = []

        async def record_invalid_model_response(self, **kwargs) -> None:
            self.invalid_responses.append(kwargs)

    model = ScriptedModelGateway(
        (
            InvalidModelResponse(
                "private raw response fragment",
                reason_code="malformed_tool_arguments_json",
                field_path="message.tool_calls[0].function.arguments",
                repairable=True,
                parser_detail="Expecting ',' delimiter",
                parser_position=12,
                raw_output_excerpt='{"meal_type":"dinner" "foods":[]}',
            ),
            text_response("已按格式重新处理。"),
        )
    )
    recorder = CapturingRecorder()

    result = await run(model, NoToolCalls(), recorder=recorder)

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert len(model.requests) == 2
    repair = model.requests[1].messages[-1]
    assert repair.role is MessageRole.SYSTEM
    assert "malformed_tool_arguments_json" in (repair.content or "")
    assert "message.tool_calls[0].function.arguments" in (repair.content or "")
    assert "Expecting ',' delimiter" in (repair.content or "")
    assert "字符位置：12" in (repair.content or "")
    assert 'meal_type\\":\\"dinner' in (repair.content or "")
    assert "private raw response fragment" not in (repair.content or "")
    assert len(recorder.invalid_responses) == 1
    assert recorder.invalid_responses[0]["call_index"] == 1
    assert recorder.invalid_responses[0]["repair_attempted"] is True
    assert (
        recorder.invalid_responses[0]["error"].raw_output_excerpt
        == '{"meal_type":"dinner" "foods":[]}'
    )


async def test_truncation_recovery_increases_output_budget():
    model = ScriptedModelGateway(
        (
            InvalidModelResponse(
                "response reached token limit",
                reason_code="output_truncated",
                repairable=True,
                output_limit_hit=True,
                finish_reason="length",
                input_tokens=3000,
                output_tokens=1024,
            ),
            text_response("已完整回复。"),
        )
    )

    result = await run(model, NoToolCalls())

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert [call.max_output_tokens for call in model.requests] == [1024, 2048]


async def test_invalid_format_retry_is_bounded_and_saves_diagnostics():
    model = ScriptedModelGateway(
        (
            InvalidModelResponse(
                "first invalid response",
                reason_code="malformed_tool_arguments_json",
                repairable=True,
            ),
            InvalidModelResponse(
                "second invalid response",
                reason_code="invalid_tool_call_shape",
                field_path="message.tool_calls[0]",
                repairable=True,
            ),
        )
    )

    result = await run(model, NoToolCalls())

    assert result.termination is HarnessTermination.FATAL_ERROR
    assert len(model.requests) == 2
    assert result.failure is not None
    assert result.failure.reason_code == "invalid_tool_call_shape"
    assert result.failure.field_path == "message.tool_calls[0]"


async def test_format_recovery_suppresses_duplicate_successful_tool_execution():
    model = ScriptedModelGateway(
        (
            call_response("first-call"),
            InvalidModelResponse(
                "malformed response after write",
                reason_code="empty_assistant_response",
                repairable=True,
            ),
            call_response("repeated-call"),
            text_response("记录已经成功。"),
        )
    )
    tool_runner = SuccessfulToolCall()

    result = await run(model, tool_runner, model_request=request(with_tool=True))

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert [call.id for call in tool_runner.calls] == ["first-call"]
    duplicate_observation = model.requests[3].messages[-1]
    assert duplicate_observation.role is MessageRole.TOOL
    assert "duplicate_successful_operation_suppressed" in (duplicate_observation.content or "")
