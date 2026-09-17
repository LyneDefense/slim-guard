from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.runtime.contracts import AgentInvocation, AgentRole, InvocationStatus
from slim_guard.runtime.invocation import InvocationRunner

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


class ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    text: str


def invocation(*, max_model_calls: int = 2, deadline: datetime | None = None) -> AgentInvocation:
    return AgentInvocation(
        invocation_id="invocation-1",
        trace_id="trace-1",
        turn_id="turn-1",
        graph_version="core-primary-v1",
        agent_role=AgentRole.CORE,
        agent_version="core-v1",
        caller="turn_harness",
        privacy_scopes=("current_user_message",),
        deadline_at=deadline or NOW + timedelta(seconds=20),
        max_model_calls=max_model_calls,
        max_tool_calls=0,
        max_total_tokens=100,
    )


def request() -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="glm-5.2",
        messages=(ModelMessage(role=MessageRole.USER, content="你好"),),
        tools=(),
        tool_choice=ToolChoice.NONE,
        response_format=ResponseFormat.JSON_OBJECT,
        output_schema_name=ExampleOutput.__name__,
    )


def response(content: str, *, tokens: int = 10) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=content),
        usage=ModelUsage(total_tokens=tokens),
    )


async def test_invocation_runner_repairs_invalid_json_once() -> None:
    model = ScriptedModelGateway(
        (response("not json"), response('{"schema_version":"1","text":"收到，我在。"}'))
    )
    runner = InvocationRunner(model=model, clock=lambda: NOW)

    result = await runner.run(
        invocation=invocation(),
        request=request(),
        output_type=ExampleOutput,
    )

    assert result.status is InvocationStatus.SUCCEEDED
    assert result.output == ExampleOutput(text="收到，我在。")
    assert result.model_call_count == 2
    assert "previous output was invalid" in (model.requests[1].messages[-1].content or "")


async def test_invocation_runner_rejects_tool_calls_and_expired_deadline() -> None:
    tool_response = ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(NormalizedToolCall(id="call-1", name="record_weight", arguments={}),),
        )
    )
    model = ScriptedModelGateway((tool_response,))
    runner = InvocationRunner(model=model, clock=lambda: NOW)

    unexpected_tool = await runner.run(
        invocation=invocation(),
        request=request(),
        output_type=ExampleOutput,
    )
    expired = await runner.run(
        invocation=invocation(deadline=NOW - timedelta(seconds=1)),
        request=request(),
        output_type=ExampleOutput,
    )

    assert unexpected_tool.failure_code == "unexpected_tool_call"
    assert expired.failure_code == "deadline_exceeded"
    assert len(model.requests) == 1
