from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def record_weight_tool() -> ToolDefinition:
    return ToolDefinition(
        name="record_weight",
        description="Record a weight explicitly provided by the current user.",
        parameters_json_schema={
            "type": "object",
            "properties": {"weight_kg": {"type": "number"}},
            "required": ["weight_kg"],
            "additionalProperties": False,
        },
        version="v1",
    )


def test_assistant_message_can_request_a_tool_without_text() -> None:
    call = NormalizedToolCall(
        id="call-1",
        name="record_weight",
        arguments={"weight_kg": 77.6},
    )

    message = ModelMessage(role=MessageRole.ASSISTANT, tool_calls=(call,))
    response = ModelResponse(message=message, finish_reason="tool_calls")

    assert response.message.tool_calls == (call,)
    assert response.message.content is None


def test_tool_result_requires_the_matching_tool_call_id() -> None:
    with pytest.raises(ValidationError, match="tool_call_id"):
        ModelMessage(role=MessageRole.TOOL, content='{"status":"succeeded"}')


def test_non_assistant_message_cannot_request_tools() -> None:
    call = NormalizedToolCall(id="call-1", name="record_weight", arguments={})

    with pytest.raises(ValidationError, match="Only assistant messages"):
        ModelMessage(
            role=MessageRole.USER,
            content="今天77.6kg",
            tool_calls=(call,),
        )


def test_required_tool_choice_needs_an_available_tool() -> None:
    with pytest.raises(ValidationError, match="requires at least one tool"):
        ModelRequest(
            purpose=ModelPurpose.HARNESS_TURN,
            model="glm-5.2",
            messages=(ModelMessage(role=MessageRole.USER, content="今天77.6kg"),),
            tool_choice=ToolChoice.REQUIRED,
        )


def test_model_request_preserves_versioned_tool_schema() -> None:
    request = ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="glm-5.2",
        messages=(ModelMessage(role=MessageRole.USER, content="今天77.6kg"),),
        tools=(record_weight_tool(),),
    )

    assert request.tool_choice is ToolChoice.AUTO
    assert request.tools[0].version == "v1"
    assert request.tools[0].parameters_json_schema["additionalProperties"] is False


def test_model_response_rejects_a_non_assistant_message() -> None:
    with pytest.raises(ValidationError, match="assistant message"):
        ModelResponse(message=ModelMessage(role=MessageRole.USER, content="invalid"))
