"""Contracts for the single-path response finalization boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.runtime.contracts import InvocationStatus


@dataclass(frozen=True, slots=True)
class ResponseFinalizationRequest:
    trace_id: str
    user_id: str
    thread_id: str
    turn_id: str
    core_invocation_id: str
    neutral_draft: str
    messages: tuple[ModelMessage, ...]
    tool_outcomes: tuple[ToolCallOutcome, ...]
    model_responses: tuple[ModelResponse, ...]
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class ResponseFinalizationResult:
    text: str
    status: InvocationStatus
    core_output_artifact_id: str
    final_output_artifact_id: str
    style_profile_version: str
    model_call_count: int = 0
    total_token_count: int = 0
    reviewer_ran: bool = False
    style_repaired: bool = False
    used_neutral_fallback: bool = False
    failure_code: str | None = None


class ResponseFinalizer(Protocol):
    async def finalize(
        self,
        request: ResponseFinalizationRequest,
    ) -> ResponseFinalizationResult: ...


__all__ = [
    "ResponseFinalizationRequest",
    "ResponseFinalizationResult",
    "ResponseFinalizer",
]
