"""Bounded JSON-only model runner used by typed workflow nodes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from slim_guard.agent_models.errors import ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.contracts import AgentInvocation, InvocationStatus
from slim_guard.orchestration.graph import InvocationGrant, validate_invocation_grant

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


@dataclass(slots=True)
class WorkflowCallBudget:
    """Task-local aggregate budget shared by all nodes and their repair calls."""

    max_model_calls: int
    max_total_tokens: int
    model_calls: int = 0
    total_tokens: int = 0


_WORKFLOW_BUDGET: ContextVar[WorkflowCallBudget | None] = ContextVar(
    "workflow_call_budget", default=None
)


@contextmanager
def workflow_call_budget(budget: WorkflowCallBudget) -> Iterator[WorkflowCallBudget]:
    token = _WORKFLOW_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _WORKFLOW_BUDGET.reset(token)


@dataclass(frozen=True, slots=True)
class StructuredRunResult(Generic[StructuredOutput]):
    status: InvocationStatus
    output: StructuredOutput | None
    responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None


class StructuredAgentRunner:
    """Runs one typed node with one schema-repair attempt and no side effects."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        request: ModelRequest,
        output_type: type[StructuredOutput],
        grant: InvocationGrant | None = None,
    ) -> StructuredRunResult[StructuredOutput]:
        if grant is not None:
            validate_invocation_grant(invocation, grant)
        if request.tools or request.tool_choice is not ToolChoice.NONE:
            raise ValueError("Structured workflow nodes cannot receive tools")
        if request.response_format is not ResponseFormat.JSON_OBJECT:
            raise ValueError("Structured workflow nodes require json_object output")
        if request.output_schema_name != output_type.__name__:
            raise ValueError("Structured request schema name does not match output type")

        responses: list[ModelResponse] = []
        last_failure = "structured_output_invalid"
        current_request = request
        for call_index in range(1, min(invocation.max_model_calls, 2) + 1):
            timeout_seconds = (invocation.deadline_at - self._clock()).total_seconds()
            if timeout_seconds <= 0:
                return self._failure(responses, "deadline_exceeded")
            budget = _WORKFLOW_BUDGET.get()
            if budget is not None:
                if budget.model_calls >= budget.max_model_calls:
                    return self._failure(responses, "turn_model_budget_exhausted")
                remaining_tokens = budget.max_total_tokens - budget.total_tokens
                if remaining_tokens <= 0:
                    return self._failure(responses, "turn_token_budget_exhausted")
                current_request = current_request.model_copy(
                    update={
                        "max_output_tokens": min(
                            current_request.max_output_tokens or remaining_tokens, remaining_tokens
                        )
                    }
                )
                # Failed provider attempts consume call budget too.
                budget.model_calls += 1
            try:
                async with asyncio.timeout(timeout_seconds):
                    response = await self._model.complete(current_request)
            except TimeoutError:
                return self._failure(responses, "deadline_exceeded")
            except ModelGatewayError:
                return self._failure(responses, "model_gateway_error")

            responses.append(response)
            if budget is not None:
                budget.total_tokens += response.usage.total_tokens
                if budget.total_tokens > budget.max_total_tokens:
                    return self._failure(responses, "turn_token_budget_exhausted")
            total_tokens = sum(item.usage.total_tokens for item in responses)
            if total_tokens > invocation.max_total_tokens:
                return self._failure(responses, "token_budget_exhausted")
            if response.message.tool_calls:
                return self._failure(responses, "unexpected_tool_call")
            content = response.message.content
            if content is None:
                last_failure = "structured_output_missing"
            else:
                try:
                    raw = json.loads(content)
                    if not isinstance(raw, dict):
                        raise ValueError("structured output must be an object")
                    output = output_type.model_validate(raw)
                except (json.JSONDecodeError, ValidationError, ValueError):
                    last_failure = "structured_output_invalid"
                else:
                    return StructuredRunResult(
                        status=InvocationStatus.SUCCEEDED,
                        output=output,
                        responses=tuple(responses),
                        model_call_count=len(responses),
                        total_token_count=total_tokens,
                    )
            if call_index >= min(invocation.max_model_calls, 2):
                break
            current_request = self._repair_request(
                request=request,
                invalid_content=content,
                output_type=output_type,
            )
        return self._failure(responses, last_failure)

    @staticmethod
    def _repair_request(
        *,
        request: ModelRequest,
        invalid_content: str | None,
        output_type: type[BaseModel],
    ) -> ModelRequest:
        schema = json.dumps(
            output_type.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        invalid = (invalid_content or "<empty>")[:12_000]
        repair_message = ModelMessage(
            role=MessageRole.USER,
            content=(
                "The previous output was invalid. Return only one JSON object that "
                f"matches this schema: {schema}\nInvalid output: {invalid}"
            ),
        )
        return request.model_copy(update={"messages": (*request.messages, repair_message)})

    @staticmethod
    def _failure(
        responses: list[ModelResponse],
        code: str,
    ) -> StructuredRunResult[StructuredOutput]:
        return StructuredRunResult(
            status=InvocationStatus.FAILED,
            output=None,
            responses=tuple(responses),
            model_call_count=len(responses),
            total_token_count=sum(item.usage.total_tokens for item in responses),
            failure_code=code,
        )


__all__ = ["StructuredAgentRunner", "StructuredRunResult"]
