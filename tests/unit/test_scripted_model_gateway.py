from __future__ import annotations

import asyncio

import pytest

from slim_guard.agent_models.errors import (
    FakeModelScriptExhausted,
    ModelGatewayClosed,
    ModelTimeoutError,
)
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)


def request(text: str) -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="fake-model",
        messages=(ModelMessage(role=MessageRole.USER, content=text),),
    )


def tool_call_response() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id="call-1",
                    name="record_weight",
                    arguments={"weight_kg": 77.6},
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


def final_response() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content="已记录今天的体重 77.6kg。",
        ),
        finish_reason="stop",
    )


async def test_script_returns_tool_call_then_final_text_and_records_requests() -> None:
    gateway: ModelGateway = ScriptedModelGateway(
        (tool_call_response(), final_response())
    )

    first_request = request("今天 77.6kg")
    second_request = request("tool result: recorded")
    first = await gateway.complete(first_request)
    second = await gateway.complete(second_request)

    assert first.message.tool_calls[0].name == "record_weight"
    assert second.message.content == "已记录今天的体重 77.6kg。"
    assert isinstance(gateway, ScriptedModelGateway)
    assert gateway.requests == [first_request, second_request]
    assert gateway.remaining_steps == 0
    gateway.assert_exhausted()


async def test_scripted_error_is_consumed_and_next_step_can_continue() -> None:
    gateway = ScriptedModelGateway(
        (
            ModelTimeoutError("planned timeout"),
            final_response(),
        )
    )

    with pytest.raises(ModelTimeoutError, match="planned timeout"):
        await gateway.complete(request("first attempt"))
    recovered = await gateway.complete(request("retry"))

    assert recovered.message.content == "已记录今天的体重 77.6kg。"
    assert len(gateway.requests) == 2
    gateway.assert_exhausted()


async def test_concurrent_calls_consume_each_script_step_once() -> None:
    gateway = ScriptedModelGateway((tool_call_response(), final_response()))

    first, second = await asyncio.gather(
        gateway.complete(request("first")),
        gateway.complete(request("second")),
    )

    assert sum(bool(item.message.tool_calls) for item in (first, second)) == 1
    assert sum(item.message.content is not None for item in (first, second)) == 1
    assert {item.messages[0].content for item in gateway.requests} == {"first", "second"}


async def test_exhausted_script_reports_the_unexpected_call_number() -> None:
    gateway = ScriptedModelGateway(())

    with pytest.raises(FakeModelScriptExhausted, match="call #1"):
        await gateway.complete(request("unexpected"))

    assert len(gateway.requests) == 1


async def test_closed_gateway_rejects_calls_without_consuming_script() -> None:
    gateway = ScriptedModelGateway((final_response(),))

    await gateway.close()
    await gateway.close()

    with pytest.raises(ModelGatewayClosed, match="closed"):
        await gateway.complete(request("after close"))
    assert gateway.closed is True
    assert gateway.requests == []
    assert gateway.remaining_steps == 1


def test_assert_exhausted_reports_unconsumed_steps() -> None:
    gateway = ScriptedModelGateway((final_response(), tool_call_response()))

    with pytest.raises(AssertionError, match="2 unconsumed"):
        gateway.assert_exhausted()
