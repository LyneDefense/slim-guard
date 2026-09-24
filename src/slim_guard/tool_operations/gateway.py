"""Compose ordinary tool attempts with the independent business-operation ledger."""

from collections.abc import Mapping
from typing import Any

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.tool_operations.contracts import OperationStore
from slim_guard.tool_operations.service import OperationService
from slim_guard.tool_operations.tools import (
    GET_OPERATION,
    LIST_OPERATIONS,
    RECOVERY_TOOL_NAMES,
    RESUME_OPERATION,
    ListOperations,
    OperationReference,
    operation_tool_definitions,
)
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolExecution,
    ToolPolicyDecision,
    ToolResult,
)
from slim_guard.tools.execution_repository import ToolExecutionStore
from slim_guard.tools.gateway import ToolExecutor, ToolGateway, ToolHandler
from slim_guard.tools.policy import ToolAuthorization, ToolPolicy
from slim_guard.tools.registry import RegisteredTool, ToolRegistry


class OperationGateway:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        executors: Mapping[str, ToolExecutor[Any]],
        execution_store: ToolExecutionStore,
        operation_store: OperationStore,
        policy: ToolPolicy,
    ) -> None:
        self._store = execution_store
        self._policy = policy
        tools = [
            registry.resolve(name) for name in registry.names if name not in RECOVERY_TOOL_NAMES
        ]
        self._operations = OperationService(
            store=operation_store,
            registry=ToolRegistry(tools),
            executors=executors,
            policy=policy,
        )
        wrapped = dict(executors)
        for tool in tools:
            if (
                tool.effect_level not in {ToolEffectLevel.READ, ToolEffectLevel.PROHIBITED}
                and not executors[tool.name].delegates_operation
            ):
                wrapped[tool.name] = ToolExecutor(
                    arguments_model=tool.arguments_model,
                    handler=self._write_handler(tool),
                    manages_timeout=True,
                )
        self._gateway = ToolGateway(
            registry=ToolRegistry(tools),
            executors=wrapped,
            execution_store=execution_store,
            policy=policy,
        )
        self._recovery_registry = ToolRegistry(
            tool for tool in operation_tool_definitions() if tool.name in registry.names
        )

    def _write_handler(self, tool: RegisteredTool) -> ToolHandler[ToolArguments]:
        async def execute(context: ToolContext, arguments: ToolArguments) -> ToolResult:
            return await self._operations.start(tool, context, arguments)

        return execute

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
    ) -> ToolExecution:
        if call.name not in self._recovery_registry.names:
            return await self._gateway.execute(
                call=call, context=context, authorization=authorization
            )

        # Per-call closures carry current trusted grants, including across concurrent
        # users. No mutable ambient authorization and no grants supplied by the model.
        recovery = self._recovery_gateway(authorization)
        return await recovery.execute(call=call, context=context, authorization=authorization)

    def preview_policy(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
    ) -> ToolPolicyDecision | None:
        if call.name not in self._recovery_registry.names:
            return self._gateway.preview_policy(
                call=call, context=context, authorization=authorization
            )
        return self._recovery_gateway(authorization).preview_policy(
            call=call, context=context, authorization=authorization
        )

    def _recovery_gateway(self, authorization: ToolAuthorization) -> ToolGateway:
        async def inspect(context: ToolContext, arguments: OperationReference) -> ToolResult:
            return await self._operations.inspect(arguments.operation_id, context, authorization)

        async def resume(context: ToolContext, arguments: OperationReference) -> ToolResult:
            return await self._operations.inspect(
                arguments.operation_id, context, authorization, resume=True
            )

        async def listing(context: ToolContext, arguments: ListOperations) -> ToolResult:
            return await self._operations.list_unsettled(context, authorization)

        executors: dict[str, ToolExecutor[Any]] = {
            GET_OPERATION: ToolExecutor(arguments_model=OperationReference, handler=inspect),
            RESUME_OPERATION: ToolExecutor(
                arguments_model=OperationReference,
                handler=resume,
                manages_timeout=True,
            ),
            LIST_OPERATIONS: ToolExecutor(arguments_model=ListOperations, handler=listing),
        }
        return ToolGateway(
            registry=self._recovery_registry,
            executors={name: executors[name] for name in self._recovery_registry.names},
            execution_store=self._store,
            policy=self._policy,
        )
