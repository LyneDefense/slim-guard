from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from slim_guard.agent_models.errors import ModelTimeoutError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.harness.events import TurnStatus, TurnTrigger
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.loop import HarnessLoop, HarnessTurnContext
from slim_guard.harness.state_repository import TurnRef
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import (
    ToolContext,
    ToolExecution,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolResult,
)
from slim_guard.tools.policy import ToolAuthorization


class RecordingToolCallRunner:
    def __init__(
        self,
        *,
        turn_status: TurnStatus = TurnStatus.RUNNING,
        failure_code: str | None = None,
        failure_retryable: bool = False,
    ) -> None:
        self.turn_status = turn_status
        self.failure_code = failure_code
        self.failure_retryable = failure_retryable
        self.calls: list[NormalizedToolCall] = []
        self.contexts: list[ToolContext] = []

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
    ) -> ToolCallOutcome:
        assert call.name in authorization.allowed_tool_names
        assert source_item_id == "user-item-1"
        assert now.tzinfo is not None
        self.calls.append(call)
        self.contexts.append(context)
        return ToolCallOutcome(
            execution=ToolExecution(
                tool_call_id=call.id,
                tool_name=call.name,
                tool_version="v1",
                canonical_arguments=call.arguments,
                idempotency_key=f"tool-{call.id}",
                policy_decision=(
                    ToolPolicyDecision.CONFIRM
                    if self.turn_status is TurnStatus.WAITING_USER_CONFIRMATION
                    else ToolPolicyDecision.ALLOW
                ),
                result=(
                    ToolResult.failed(
                        code=(self.failure_code or "tool_confirmation_required"),
                        message=(
                            "The tool failed."
                            if self.failure_code is not None
                            else "Please confirm this action."
                        ),
                        retryable=self.failure_retryable,
                    )
                    if self.turn_status is TurnStatus.WAITING_USER_CONFIRMATION
                    or self.failure_code is not None
                    else ToolResult.success(
                        output={"recorded": True, **call.arguments},
                        source_ids=(f"record-{call.id}",),
                    )
                ),
            ),
            turn=TurnRef(
                id=context.turn_id,
                thread_id=context.thread_id,
                agent_version_id=context.agent_version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                status=self.turn_status,
                deadline_at=None,
                completed_at=None,
            ),
            pending_action=None,
        )


def tool_call(call_id: str, *, name: str = "record_weight") -> NormalizedToolCall:
    return NormalizedToolCall(
        id=call_id,
        name=name,
        arguments={"weight_kg": 77.6},
    )


def assistant_tool_calls(
    *calls: NormalizedToolCall,
    total_tokens: int = 0,
) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=calls,
        ),
        finish_reason="tool_calls",
        usage=ModelUsage(total_tokens=total_tokens),
    )


def assistant_text(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


def request() -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="fake-model",
        messages=(
            ModelMessage(role=MessageRole.SYSTEM, content="You are SlimGuard."),
            ModelMessage(role=MessageRole.USER, content="今天 77.6kg"),
        ),
        tools=(
            ToolDefinition(
                name="record_weight",
                description="Record the current user's weight.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"weight_kg": {"type": "number"}},
                    "required": ["weight_kg"],
                    "additionalProperties": False,
                },
                version="v1",
            ),
        ),
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


async def run_loop(
    model: ScriptedModelGateway,
    tools: RecordingToolCallRunner,
    *,
    limits: HarnessLimits | None = None,
    turn_context: HarnessTurnContext | None = None,
    clock: Callable[[], datetime] | None = None,
):
    return await HarnessLoop(
        model=model,
        tool_calls=tools,
        limits=limits or HarnessLimits(),
        clock=clock,
    ).run(
        request=request(),
        context=turn_context or context(),
        authorization=authorization(),
        source_item_id="user-item-1",
        now=datetime.now(UTC),
    )


async def test_loop_executes_tool_and_returns_one_final_response() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_text("已记录今天的体重 77.6kg。"),
        )
    )
    tools = RecordingToolCallRunner()

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert result.final_text == "已记录今天的体重 77.6kg。"
    assert result.model_call_count == 2
    assert result.tool_call_count == 1
    assert tools.calls[0].name == "record_weight"
    second_messages = model.requests[1].messages
    assert [message.role for message in second_messages[-2:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    observation = json.loads(second_messages[-1].content or "")
    assert observation["status"] == "succeeded"
    assert observation["output"]["weight_kg"] == 77.6


async def test_loop_executes_multiple_tool_calls_before_next_model_call() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(
                tool_call("call-1"),
                tool_call("call-2"),
            ),
            assistant_text("两项记录均已处理。"),
        )
    )
    tools = RecordingToolCallRunner()

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert result.tool_call_count == 2
    assert [call.id for call in tools.calls] == ["call-1", "call-2"]
    assert [message.role for message in model.requests[1].messages[-3:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.TOOL,
    ]


async def test_loop_pauses_immediately_when_tool_requires_confirmation() -> None:
    model = ScriptedModelGateway((assistant_tool_calls(tool_call("call-1")),))
    tools = RecordingToolCallRunner(
        turn_status=TurnStatus.WAITING_USER_CONFIRMATION
    )

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.WAITING_USER_CONFIRMATION
    assert result.final_text is None
    assert result.model_call_count == 1
    assert result.tool_call_count == 1
    model.assert_exhausted()


async def test_tool_failure_is_returned_to_model_as_an_observation() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_text("这次没有记录成功，请稍后重试。"),
        )
    )
    tools = RecordingToolCallRunner(failure_code="database_unavailable")

    result = await run_loop(model, tools)

    observation = json.loads(model.requests[1].messages[-1].content or "")
    assert observation["status"] == "failed"
    assert observation["failure"]["code"] == "database_unavailable"
    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert result.final_text == "这次没有记录成功，请稍后重试。"


async def test_tool_failure_emits_structured_warning_without_arguments(caplog) -> None:
    caplog.set_level("WARNING")
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_text("这次没有记录成功。"),
        )
    )
    tools = RecordingToolCallRunner(failure_code="database_unavailable")

    await run_loop(model, tools)

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "slim_guard_tool_call_failed"
    )
    assert record.tool_name == "record_weight"
    assert record.failure_code == "database_unavailable"
    assert record.retryable is False
    assert "77.6" not in record.getMessage()


async def test_repeated_nonretryable_failure_forces_model_authored_text_response() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_tool_calls(tool_call("call-2")),
            assistant_text("这次没有保存成功，我不会继续重复尝试。"),
        )
    )
    tools = RecordingToolCallRunner(failure_code="invalid_arguments")

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert result.final_text == "这次没有保存成功，我不会继续重复尝试。"
    assert result.model_call_count == 3
    assert result.tool_call_count == 2
    assert model.requests[2].tools == ()
    assert model.requests[2].tool_choice is ToolChoice.NONE


async def test_retryable_failures_do_not_remove_model_tools() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_tool_calls(tool_call("call-2")),
            assistant_text("服务暂时不可用。"),
        )
    )
    tools = RecordingToolCallRunner(
        failure_code="database_unavailable",
        failure_retryable=True,
    )

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.FINAL_RESPONSE
    assert model.requests[2].tools == request().tools
    assert model.requests[2].tool_choice is ToolChoice.AUTO


async def test_model_call_limit_stops_an_infinite_tool_loop() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(tool_call("call-1")),
            assistant_tool_calls(tool_call("call-2")),
        )
    )
    tools = RecordingToolCallRunner()

    result = await run_loop(
        model,
        tools,
        limits=HarnessLimits(max_model_calls=1, max_tool_calls=8),
    )

    assert result.termination is HarnessTermination.MAX_MODEL_CALLS
    assert result.model_call_count == 1
    assert result.tool_call_count == 1
    assert model.remaining_steps == 1


async def test_tool_call_limit_rejects_the_whole_oversized_batch() -> None:
    model = ScriptedModelGateway(
        (
            assistant_tool_calls(
                tool_call("call-1"),
                tool_call("call-2"),
            ),
        )
    )
    tools = RecordingToolCallRunner()

    result = await run_loop(
        model,
        tools,
        limits=HarnessLimits(max_model_calls=2, max_tool_calls=1),
    )

    assert result.termination is HarnessTermination.MAX_TOOL_CALLS
    assert result.model_call_count == 1
    assert result.tool_call_count == 0
    assert tools.calls == []


async def test_total_token_limit_stops_before_executing_returned_tools() -> None:
    model = ScriptedModelGateway(
        (assistant_tool_calls(tool_call("call-1"), total_tokens=11),)
    )
    tools = RecordingToolCallRunner()

    result = await run_loop(
        model,
        tools,
        limits=HarnessLimits(
            max_model_calls=2,
            max_tool_calls=2,
            max_total_tokens=10,
        ),
    )

    assert result.termination is HarnessTermination.MAX_TOTAL_TOKENS
    assert result.total_token_count == 11
    assert result.tool_call_count == 0
    assert tools.calls == []


async def test_expired_deadline_stops_before_first_model_call() -> None:
    current_time = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
    model = ScriptedModelGateway((assistant_text("不应被调用"),))
    tools = RecordingToolCallRunner()
    turn_context = context()
    turn_context = HarnessTurnContext(
        thread_id=turn_context.thread_id,
        turn_id=turn_context.turn_id,
        user_id=turn_context.user_id,
        agent_version_id=turn_context.agent_version_id,
        execution_mode=turn_context.execution_mode,
        deadline_at=current_time - timedelta(seconds=1),
    )

    result = await run_loop(
        model,
        tools,
        turn_context=turn_context,
        clock=lambda: current_time,
    )

    assert result.termination is HarnessTermination.DEADLINE_EXCEEDED
    assert result.model_call_count == 0
    assert len(model.requests) == 0
    assert model.remaining_steps == 1


async def test_model_gateway_error_returns_sanitized_failure() -> None:
    model = ScriptedModelGateway((ModelTimeoutError("secret provider response"),))
    tools = RecordingToolCallRunner()

    result = await run_loop(model, tools)

    assert result.termination is HarnessTermination.FATAL_ERROR
    assert result.final_text is None
    assert result.failure is not None
    assert result.failure.code == "model_timeout"
    assert result.failure.error_type == "ModelTimeoutError"
    assert result.failure.retryable is True
    assert "secret" not in str(result.failure.to_payload())
