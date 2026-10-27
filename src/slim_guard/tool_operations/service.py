"""Execute immutable commands and recover only explicitly referenced operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from slim_guard.tool_operations.contracts import OperationStore, ToolOperation
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectStatus,
    ToolOperationReceipt,
    ToolOperationStatus,
    ToolPolicyDecision,
    ToolResult,
    ToolResultStatus,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.policy import ToolAuthorization, ToolPolicy
from slim_guard.tools.registry import RegisteredTool, ToolRegistry

logger = logging.getLogger(__name__)


class OperationService:
    def __init__(
        self,
        *,
        store: OperationStore,
        registry: ToolRegistry,
        executors: Mapping[str, ToolExecutor[Any]],
        policy: ToolPolicy,
    ) -> None:
        self.store = store
        self.registry = registry
        self.executors = executors
        self.policy = policy

    async def lookup_execution_result(self, context: ToolContext) -> ToolResult | None:
        """Resolve a replayed tool attempt from its durable business-operation record."""
        execution_key = context.execution_idempotency_key
        if execution_key is None:
            return None
        operation = await self.store.get_by_initial_execution_key(
            execution_key, context=context
        )
        if operation is None:
            return None
        operation = await self._reconcile(operation)
        if operation.status == "succeeded" and operation.result is not None:
            return operation.result
        return None

    async def start(
        self,
        tool: RegisteredTool,
        context: ToolContext,
        arguments: ToolArguments,
    ) -> ToolResult:
        claim = await self.store.start(
            tool_name=tool.name,
            tool_version=tool.version,
            context=context,
            arguments=arguments.model_dump(mode="json"),
        )
        if not claim.acquired:
            # A different invocation is not silently equated to this operation.
            return self._referenced_failure(
                claim.operation,
                "unsettled_write_exists",
                "A write is already running or has an uncertain result for this capability. "
                "This new call was not executed. Inspect the referenced operation.",
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        return await self._invoke(claim.operation, tool)

    async def inspect(
        self,
        operation_id: str,
        context: ToolContext,
        authorization: ToolAuthorization,
        *,
        resume: bool = False,
    ) -> ToolResult:
        operation = await self.store.get(operation_id, context=context)
        if operation is None:
            return self._failure("operation_not_available", "No accessible operation was found.")
        if operation.arguments.get("_redacted") is True:
            return self._referenced_failure(
                operation,
                "operation_payload_expired",
                "This operation's command was removed by retention; it cannot be resumed.",
            )
        if operation.tool_name not in self.registry.names:
            return self._referenced_failure(
                operation, "operation_tool_unavailable", "The original tool is no longer available."
            )
        tool = self.registry.resolve(operation.tool_name)
        if tool.name not in authorization.allowed_tool_names:
            return self._failure(
                "tool_not_authorized", "The original capability is not authorized."
            )
        if tool.version != operation.tool_version:
            return self._referenced_failure(
                operation,
                "operation_version_changed",
                "The original tool contract is no longer active.",
            )
        operation = await self._reconcile(operation)
        if not resume:
            return ToolResult.success(output={"operation": self._view(operation)})
        policy = self.policy.evaluate(
            tool=tool,
            context=context,
            execution_key=operation.initial_execution_key,
            authorization=authorization,
        )
        if policy.decision is not ToolPolicyDecision.ALLOW:
            return self._referenced_failure(operation, policy.code, policy.reason)
        if operation.status == "succeeded" and operation.result is not None:
            return operation.result
        if operation.status != "not_applied":
            return self._referenced_failure(
                operation,
                "operation_result_uncertain",
                "The write may still be running or already applied; it was not executed again.",
            )
        failure = operation.result.failure if operation.result else None
        if failure is None or not failure.retryable:
            return self._referenced_failure(
                operation,
                "operation_not_retryable",
                "The frozen command cannot be retried unchanged. Do not alter it through resume.",
            )
        if not await self.store.acquire_retry(operation):
            return self._referenced_failure(
                operation,
                "operation_state_changed",
                "The operation state changed; no retry was started.",
            )
        attempt = operation.model_copy(
            update={"attempt": operation.attempt + 1, "status": "running"}
        )
        return await self._invoke(attempt, tool)

    async def _reconcile(self, operation: ToolOperation) -> ToolOperation:
        lookup = self.executors[operation.tool_name].lookup_result
        if operation.status not in {"running", "unknown"} or lookup is None:
            return operation
        try:
            async with asyncio.timeout(self.registry.resolve(operation.tool_name).timeout_seconds):
                result = await lookup(operation.context)
        except Exception as exc:
            logger.warning(
                "operation_lookup_failed",
                extra={"operation_id": operation.id, "error_type": type(exc).__name__},
            )
            return operation
        if result is None or result.status is not ToolResultStatus.SUCCEEDED:
            return operation
        result = result.model_copy(
            update={
                "operation": ToolOperationReceipt(
                    operation_id=operation.id,
                    tool_name=operation.tool_name,
                    status="succeeded",
                )
            }
        )
        if await self.store.reconcile_success(operation, result):
            return operation.model_copy(update={"status": "succeeded", "result": result})
        return await self.store.get(operation.id, context=operation.context) or operation

    async def list_unsettled(
        self,
        context: ToolContext,
        authorization: ToolAuthorization,
    ) -> ToolResult:
        operations = await self.store.list_unsettled(
            user_id=context.user_id,
            thread_id=context.thread_id,
            execution_mode=context.execution_mode,
        )
        return ToolResult.success(
            output={
                "operations": [
                    self._view(op)
                    for op in operations
                    if op.tool_name in authorization.allowed_tool_names
                ]
            }
        )

    async def _invoke(self, operation: ToolOperation, tool: RegisteredTool) -> ToolResult:
        executor = self.executors[tool.name]
        arguments = executor.validate(operation.arguments)
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                result = await executor.invoke(operation.context, arguments)
        except TimeoutError:
            result = ToolResult.failed(
                code="tool_timeout",
                message="The write timed out; "
                "its effect is unknown. It must not be blindly repeated.",
            )
        except Exception as exc:
            logger.error(
                "tool_operation_failed",
                extra={
                    "operation_id": operation.id,
                    "error_type": type(exc).__name__,
                    "tool_name": tool.name,
                },
            )
            result = ToolResult.failed(
                code="tool_execution_failed", message="The write did not return a confirmed result."
            )
        # Cancellation deliberately leaves a running/uncertain durable claim. No
        # timeout, exception, missing receipt or age can prove a write didn't happen.
        status = ToolOperationStatus.SUCCEEDED
        if result.status is ToolResultStatus.FAILED:
            status = (
                ToolOperationStatus.NOT_APPLIED
                if result.failure is not None
                and result.failure.effect_status is ToolEffectStatus.NOT_APPLIED
                else ToolOperationStatus.UNKNOWN
            )
        result = result.model_copy(
            update={
                "operation": ToolOperationReceipt(
                    operation_id=operation.id,
                    tool_name=operation.tool_name,
                    status=status,
                )
            }
        )
        await self.store.finish(operation, status=status, result=result)
        return result

    @staticmethod
    def _view(operation: ToolOperation) -> dict[str, Any]:
        return {
            "operation_id": operation.id,
            "tool_name": operation.tool_name,
            "status": operation.status,
            "attempt": operation.attempt,
            "source_turn_id": operation.context.turn_id,
            "source_item_id": operation.context.source_item_id,
            "arguments": operation.arguments,
            "result": operation.result.model_dump(mode="json") if operation.result else None,
        }

    @staticmethod
    def _failure(code: str, message: str) -> ToolResult:
        return ToolResult.failed(
            code=code, message=message, effect_status=ToolEffectStatus.NOT_APPLIED
        )

    @staticmethod
    def _referenced_failure(
        operation: ToolOperation,
        code: str,
        message: str,
        *,
        effect_status: ToolEffectStatus = ToolEffectStatus.NOT_APPLIED,
    ) -> ToolResult:
        return ToolResult.failed(
            code=code, message=message, effect_status=effect_status
        ).model_copy(
            update={
                "operation": ToolOperationReceipt(
                    operation_id=operation.id,
                    tool_name=operation.tool_name,
                    status=operation.status,
                )
            },
        )
