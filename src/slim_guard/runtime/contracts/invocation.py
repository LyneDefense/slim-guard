"""Typed envelopes for one bounded agent invocation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import AliasChoices, Field, field_validator, model_validator

from slim_guard.runtime.contracts.base import ContractModel


class AgentRole(StrEnum):
    CORE = "core"
    ORCHESTRATOR = "orchestrator"
    DISH_RECOGNITION = "dish_recognition"
    NUTRITION_RETRIEVAL = "nutrition_retrieval"
    NUTRITION_EXPERT = "nutrition_expert"
    RESPONSE_STYLE = "response_style"
    RESPONSE_REVIEWER = "response_reviewer"


class InvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class AgentInvocation(ContractModel):
    """Runtime-created envelope for one bounded agent attempt."""

    invocation_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=128)
    agent_role: AgentRole = Field(validation_alias=AliasChoices("agent_role", "callee"))
    agent_version: str = Field(min_length=1, max_length=128)
    attempt: int = Field(default=1, ge=1, le=16, strict=True)
    caller: str = Field(default="coordinator", min_length=1, max_length=128)
    parent_invocation_id: str | None = Field(default=None, min_length=1, max_length=128)
    input_artifact_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("input_artifact_ids", "parent_artifact_ids"),
        max_length=128,
    )
    input_schema: str | None = Field(default=None, min_length=1, max_length=128)
    input_schema_version: str = Field(default="1", min_length=1, max_length=32)
    allowed_tools: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("allowed_tools", "allowed_tool_names"),
        max_length=64,
    )
    privacy_scopes: tuple[str, ...] = Field(default=(), max_length=64)
    deadline_at: datetime
    max_model_calls: int = Field(ge=1, le=32, strict=True)
    max_tool_calls: int = Field(ge=0, le=64, strict=True)
    max_total_tokens: int = Field(ge=1, le=10_000_000, strict=True)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("deadline_at")
    @classmethod
    def validate_deadline(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Invocation deadline must be timezone-aware")
        return value

    @field_validator("input_artifact_ids", "allowed_tools", "privacy_scopes")
    @classmethod
    def reject_duplicate_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Invocation references and grants cannot contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("Invocation references and grants must be unique")
        return value

    @property
    def callee(self) -> AgentRole:
        return self.agent_role

    @property
    def parent_artifact_ids(self) -> tuple[str, ...]:
        return self.input_artifact_ids

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return self.allowed_tools


Invocation = AgentInvocation


class AgentResult(ContractModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    status: InvocationStatus
    output_schema: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=32)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_receipt_ids: tuple[str, ...] = Field(default=(), max_length=64)
    model_call_count: int = Field(ge=0, le=32, strict=True)
    tool_call_count: int = Field(ge=0, le=64, strict=True)
    token_usage: int = Field(ge=0, le=10_000_000, strict=True)
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is InvocationStatus.SUCCEEDED:
            if self.artifact_id is None:
                raise ValueError("A succeeded invocation requires an artifact_id")
            if self.failure_code is not None:
                raise ValueError("A succeeded invocation cannot have a failure_code")
        elif self.status is InvocationStatus.FAILED and self.failure_code is None:
            raise ValueError("A failed invocation requires a failure_code")
        return self


__all__ = [
    "AgentInvocation",
    "AgentResult",
    "AgentRole",
    "Invocation",
    "InvocationStatus",
]
