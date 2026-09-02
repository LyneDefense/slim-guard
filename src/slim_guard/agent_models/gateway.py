from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelPurpose(StrEnum):
    HARNESS_TURN = "harness_turn"
    MEMORY_INGESTION = "memory_ingestion"
    MEMORY_RECALL = "memory_recall"
    VISION_INSPECTION = "vision_inspection"
    EVALUATION = "evaluation"
    IMPROVEMENT = "improvement"


class ToolChoice(StrEnum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"


class NormalizedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: str | None = None
    tool_calls: tuple[NormalizedToolCall, ...] = ()
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_payload(self) -> Self:
        if self.role is MessageRole.TOOL:
            if not self.tool_call_id:
                raise ValueError("Tool messages require tool_call_id")
            if self.content is None:
                raise ValueError("Tool messages require content")
            if self.tool_calls:
                raise ValueError("Tool messages cannot contain tool_calls")
            return self
        if self.tool_call_id is not None:
            raise ValueError("Only tool messages can contain tool_call_id")
        if self.tool_calls and self.role is not MessageRole.ASSISTANT:
            raise ValueError("Only assistant messages can contain tool_calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("Messages require content or tool_calls")
        return self


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1, max_length=4096)
    parameters_json_schema: dict[str, Any]
    strict: bool = True
    version: str = Field(min_length=1, max_length=128)


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: ModelPurpose
    model: str = Field(min_length=1, max_length=256)
    messages: tuple[ModelMessage, ...] = Field(min_length=1)
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: ToolChoice = ToolChoice.AUTO
    max_output_tokens: int = Field(default=1024, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_tool_choice(self) -> Self:
        if self.tool_choice is ToolChoice.REQUIRED and not self.tools:
            raise ValueError("tool_choice=required requires at least one tool")
        return self


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: ModelMessage
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    provider_request_id: str | None = None

    @model_validator(mode="after")
    def validate_assistant_message(self) -> Self:
        if self.message.role is not MessageRole.ASSISTANT:
            raise ValueError("Model responses require an assistant message")
        return self


class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def close(self) -> None: ...
