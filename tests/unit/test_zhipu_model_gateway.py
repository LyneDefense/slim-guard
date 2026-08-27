from __future__ import annotations

import json

import httpx
import pytest

from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelProviderError,
    ModelTimeoutError,
    UnsupportedModelFeature,
)
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    NormalizedToolCall,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.agent_models.zhipu import ZhipuModelGateway


def record_weight_tool() -> ToolDefinition:
    return ToolDefinition(
        name="record_weight",
        description="Record the current user's weight.",
        parameters_json_schema={
            "type": "object",
            "properties": {"weight_kg": {"type": "number"}},
            "required": ["weight_kg"],
            "additionalProperties": False,
        },
        version="v1",
    )


def request_with(
    *messages: ModelMessage,
    tool_choice: ToolChoice = ToolChoice.AUTO,
) -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="glm-5.2",
        messages=messages,
        tools=(record_weight_tool(),),
        tool_choice=tool_choice,
        max_output_tokens=512,
        metadata={"user_id": "anonymous-user"},
    )


async def test_gateway_serializes_tools_and_parses_tool_calls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "provider-response-1",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "record_weight",
                                        "arguments": '{"weight_kg":77.6}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await gateway.complete(
            request_with(ModelMessage(role=MessageRole.USER, content="今天77.6kg"))
        )
    finally:
        await gateway.close()

    body = json.loads(requests[0].content)
    assert body["model"] == "glm-5.2"
    assert body["thinking"] == {"type": "disabled"}
    assert body["do_sample"] is False
    assert body["stream"] is False
    assert body["tool_choice"] == "auto"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "record_weight",
                "description": "Record the current user's weight.",
                "parameters": record_weight_tool().parameters_json_schema,
            },
        }
    ]
    assert body["user_id"] == "anonymous-user"
    assert response.provider_request_id == "provider-response-1"
    assert response.message.tool_calls[0].name == "record_weight"
    assert response.message.tool_calls[0].arguments == {"weight_kg": 77.6}
    assert response.usage.total_tokens == 120


async def test_gateway_serializes_assistant_call_and_tool_result() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": " 已记录77.6kg。 "},
                    }
                ]
            },
        )

    call = NormalizedToolCall(
        id="call-1",
        name="record_weight",
        arguments={"weight_kg": 77.6},
    )
    gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await gateway.complete(
            request_with(
                ModelMessage(role=MessageRole.USER, content="今天77.6kg"),
                ModelMessage(role=MessageRole.ASSISTANT, tool_calls=(call,)),
                ModelMessage(
                    role=MessageRole.TOOL,
                    tool_call_id="call-1",
                    content='{"status":"succeeded"}',
                ),
            )
        )
    finally:
        await gateway.close()

    messages = captured["messages"]
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == '{"weight_kg":77.6}'  # type: ignore[index]
    assert messages[2] == {  # type: ignore[index]
        "role": "tool",
        "content": '{"status":"succeeded"}',
        "tool_call_id": "call-1",
    }
    assert response.message.content == "已记录77.6kg。"
    assert response.message.tool_calls == ()


async def test_gateway_rejects_tool_arguments_that_are_not_an_object() -> None:
    gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "record_weight",
                                            "arguments": "[77.6]",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        ),
    )
    try:
        with pytest.raises(InvalidModelResponse, match="arguments are not an object"):
            await gateway.complete(
                request_with(ModelMessage(role=MessageRole.USER, content="今天77.6kg"))
            )
    finally:
        await gateway.close()


async def test_gateway_normalizes_timeout_and_http_errors() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    timeout_gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(timeout),
    )
    try:
        with pytest.raises(ModelTimeoutError):
            await timeout_gateway.complete(
                request_with(ModelMessage(role=MessageRole.USER, content="hello"))
            )
    finally:
        await timeout_gateway.close()

    error_gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(429)),
    )
    try:
        with pytest.raises(ModelProviderError) as caught:
            await error_gateway.complete(
                request_with(ModelMessage(role=MessageRole.USER, content="hello"))
            )
        assert caught.value.status_code == 429
    finally:
        await error_gateway.close()


async def test_gateway_rejects_required_tool_choice_before_request() -> None:
    gateway = ZhipuModelGateway(
        api_key="secret-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    try:
        with pytest.raises(UnsupportedModelFeature, match="tool_choice=required"):
            await gateway.complete(
                request_with(
                    ModelMessage(role=MessageRole.USER, content="今天77.6kg"),
                    tool_choice=ToolChoice.REQUIRED,
                )
            )
    finally:
        await gateway.close()
