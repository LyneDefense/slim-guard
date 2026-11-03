"""Bounded, authorized runner for schema-constrained Agent invocations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

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
from slim_guard.runtime.contracts import AgentInvocation, InvocationStatus
from slim_guard.runtime.invocation.grants import InvocationGrant, validate_invocation_grant
from slim_guard.runtime.invocation.validation import parse_output, repair_content
from slim_guard.usage.context import usage_operation

InvocationOutput = TypeVar("InvocationOutput", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class InvocationRunResult(Generic[InvocationOutput]):
    status: InvocationStatus
    output: InvocationOutput | None
    responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None
    validation_errors: tuple[dict[str, object], ...] = ()


class InvocationRunner:
    """Run a typed, zero-tool invocation with one schema-repair attempt."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._clock = clock or (lambda: datetime.now(UTC))

    @usage_operation("agent_generation", source="invocation", kind="agent")
    async def run(
        self,
        *,
        invocation: AgentInvocation,
        request: ModelRequest,
        output_type: type[InvocationOutput],
        grant: InvocationGrant | None = None,
        repair_schema: bool = True,
        validate_output: Callable[[InvocationOutput], list[dict[str, object]]] | None = None,
    ) -> InvocationRunResult[InvocationOutput]:
        if grant is not None:
            validate_invocation_grant(invocation, grant)
        if request.tools or request.tool_choice is not ToolChoice.NONE:
            raise ValueError("Structured Agent invocations cannot receive tools")
        if request.response_format is not ResponseFormat.JSON_OBJECT:
            raise ValueError("Structured Agent invocations require json_object output")
        if request.output_schema_name != output_type.__name__:
            raise ValueError("Invocation request schema name does not match output type")

        responses: list[ModelResponse] = []
        last_failure = "structured_output_invalid"
        current_request = request
        diagnostics: list[dict[str, object]] = []
        for call_index in range(1, (2 if repair_schema else 1) + 1):
            timeout_seconds = (invocation.deadline_at - self._clock()).total_seconds()
            if timeout_seconds <= 0:
                return self._failure(responses, "deadline_exceeded", tuple(diagnostics))
            try:
                async with asyncio.timeout(timeout_seconds):
                    response = await self._model.complete(current_request)
            except TimeoutError:
                return self._failure(responses, "deadline_exceeded", tuple(diagnostics))
            except ModelGatewayError:
                return self._failure(responses, "model_gateway_error", tuple(diagnostics))

            responses.append(response)
            total_tokens = sum(item.usage.total_tokens for item in responses)
            if response.message.tool_calls:
                return self._failure(responses, "unexpected_tool_call", tuple(diagnostics))
            content = response.message.content
            output, errors = parse_output(content, output_type)
            if output is not None and validate_output is not None:
                errors = validate_output(output)
                if errors:
                    output = None
            if output is not None:
                return InvocationRunResult(
                    status=InvocationStatus.SUCCEEDED,
                    output=output,
                    responses=tuple(responses),
                    model_call_count=len(responses),
                    total_token_count=total_tokens,
                    validation_errors=tuple(diagnostics),
                )
            last_failure = (
                "structured_output_missing" if content is None else "structured_output_invalid"
            )
            diagnostics.append(
                {"attempt": call_index, "schema": output_type.__name__, "errors": errors}
            )
            if not repair_schema or call_index == 2:
                break
            current_request = self._repair_request(
                request=request,
                invalid_content=content,
                output_type=output_type,
                errors=errors,
            )
        return self._failure(responses, last_failure, tuple(diagnostics))

    @staticmethod
    def _repair_request(
        *,
        request: ModelRequest,
        invalid_content: str | None,
        output_type: type[BaseModel],
        errors: list[dict[str, object]],
    ) -> ModelRequest:
        repair_message = ModelMessage(
            role=MessageRole.USER,
            content=repair_content(output_type, invalid_content, errors),
        )
        return request.model_copy(update={"messages": (*request.messages, repair_message)})

    @staticmethod
    def _failure(
        responses: list[ModelResponse],
        code: str,
        validation_errors: tuple[dict[str, object], ...] = (),
    ) -> InvocationRunResult[InvocationOutput]:
        return InvocationRunResult(
            status=InvocationStatus.FAILED,
            output=None,
            responses=tuple(responses),
            model_call_count=len(responses),
            total_token_count=sum(item.usage.total_tokens for item in responses),
            failure_code=code,
            validation_errors=validation_errors,
        )
