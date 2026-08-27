from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolArguments(BaseModel):
    """Base class for every model-supplied tool argument object."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ToolEffectLevel(StrEnum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    SENSITIVE_WRITE = "sensitive_write"
    EXTERNAL_EFFECT = "external_effect"
    PROHIBITED = "prohibited"


class ToolExecutionMode(StrEnum):
    LIVE = "live"
    SHADOW = "shadow"
    EVALUATION = "evaluation"


class ToolContext(BaseModel):
    """Identity and isolation information supplied by the Harness, never the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=128)
    agent_version_id: str = Field(min_length=1, max_length=128)
    execution_mode: ToolExecutionMode


class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1024)
    retryable: bool = False


class ToolResult(BaseModel):
    """Normalized observation returned to the model after a tool attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    source_ids: tuple[str, ...] = ()
    failure: ToolFailure | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is ToolResultStatus.SUCCEEDED and self.failure is not None:
            raise ValueError("Successful tool results cannot contain a failure")
        if self.status is ToolResultStatus.FAILED and self.failure is None:
            raise ValueError("Failed tool results require a failure")
        return self

    @classmethod
    def success(
        cls,
        *,
        output: dict[str, Any],
        source_ids: tuple[str, ...] = (),
    ) -> ToolResult:
        return cls(
            status=ToolResultStatus.SUCCEEDED,
            output=output,
            source_ids=source_ids,
        )

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> ToolResult:
        return cls(
            status=ToolResultStatus.FAILED,
            failure=ToolFailure(code=code, message=message, retryable=retryable),
        )

    def to_model_content(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
