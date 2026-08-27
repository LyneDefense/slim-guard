from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from pydantic import ValidationError

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolExecution,
    ToolResult,
)
from slim_guard.tools.errors import (
    ToolContextMismatchError,
    ToolGatewayConfigurationError,
    UnknownToolError,
)
from slim_guard.tools.registry import RegisteredTool, ToolRegistry

logger = logging.getLogger(__name__)

ArgumentsT = TypeVar("ArgumentsT", bound=ToolArguments)
ArgumentsT_contra = TypeVar("ArgumentsT_contra", bound=ToolArguments, contravariant=True)


class ToolHandler(Protocol[ArgumentsT_contra]):
    async def __call__(
        self,
        context: ToolContext,
        arguments: ArgumentsT_contra,
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolExecutor(Generic[ArgumentsT]):
    arguments_model: type[ArgumentsT]
    handler: ToolHandler[ArgumentsT]

    async def invoke(
        self,
        context: ToolContext,
        raw_arguments: dict[str, Any],
    ) -> tuple[ArgumentsT, ToolResult]:
        arguments = self.arguments_model.model_validate(raw_arguments)
        result = await self.handler(context, arguments)
        return arguments, result


class ToolGateway:
    """Validates and routes model tool calls through explicitly bound executors."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        executors: Mapping[str, ToolExecutor[Any]],
    ) -> None:
        self._registry = registry
        self._executors = dict(executors)
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        registered_names = set(self._registry.names)
        executor_names = set(self._executors)
        if missing := sorted(registered_names - executor_names):
            raise ToolGatewayConfigurationError(
                f"Registered tools are missing executors: {', '.join(missing)}"
            )
        if unknown := sorted(executor_names - registered_names):
            raise ToolGatewayConfigurationError(
                f"Executors reference unregistered tools: {', '.join(unknown)}"
            )
        for name in self._registry.names:
            tool = self._registry.resolve(name)
            executor = self._executors[name]
            if executor.arguments_model is not tool.arguments_model:
                raise ToolGatewayConfigurationError(
                    f"Executor argument model does not match registered tool: {name}"
                )

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
    ) -> ToolExecution:
        if call.id != context.tool_call_id:
            raise ToolContextMismatchError(
                "Tool context call ID does not match the requested tool call"
            )
        try:
            tool = self._registry.resolve(call.name)
        except UnknownToolError:
            return self._failure(
                call=call,
                code="unknown_tool",
                message=f"The tool '{call.name}' is not available.",
            )

        executor = self._executors[tool.name]
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                arguments, result = await executor.invoke(context, call.arguments)
        except ValidationError as exc:
            return self._failure(
                call=call,
                tool=tool,
                code="invalid_arguments",
                message=self._validation_message(exc),
            )
        except TimeoutError:
            return self._failure(
                call=call,
                tool=tool,
                code="tool_timeout",
                message=f"The tool '{tool.name}' timed out.",
                retryable=True,
            )
        except Exception as exc:
            logger.error(
                "tool_execution_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "tool_name": tool.name,
                    "tool_version": tool.version,
                    "tool_call_id": call.id,
                    "turn_id": context.turn_id,
                },
            )
            return self._failure(
                call=call,
                tool=tool,
                code="tool_execution_failed",
                message=f"The tool '{tool.name}' could not complete.",
                retryable=False,
            )

        canonical_arguments = arguments.model_dump(mode="json")
        return ToolExecution(
            tool_call_id=call.id,
            tool_name=tool.name,
            tool_version=tool.version,
            canonical_arguments=canonical_arguments,
            idempotency_key=self._idempotency_key(
                context=context,
                tool=tool,
                arguments=canonical_arguments,
            ),
            result=result,
        )

    @staticmethod
    def _validation_message(exc: ValidationError) -> str:
        issues = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in error["loc"]) or "arguments"
            issues.append(f"{location}: {error['msg']}")
        return ("Invalid tool arguments. " + "; ".join(issues))[:1024]

    @staticmethod
    def _idempotency_key(
        *,
        context: ToolContext,
        tool: RegisteredTool,
        arguments: dict[str, Any],
    ) -> str:
        material = json.dumps(
            {
                "arguments": arguments,
                "tool_call_id": context.tool_call_id,
                "tool_name": tool.name,
                "tool_version": tool.version,
                "turn_id": context.turn_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"tool-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _failure(
        *,
        call: NormalizedToolCall,
        code: str,
        message: str,
        tool: RegisteredTool | None = None,
        retryable: bool = False,
    ) -> ToolExecution:
        return ToolExecution(
            tool_call_id=call.id,
            tool_name=call.name,
            tool_version=tool.version if tool is not None else None,
            result=ToolResult.failed(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )
