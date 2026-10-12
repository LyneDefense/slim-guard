"""Wire tool execution and operation recovery using one shared registry."""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from slim_guard.db.session import Database
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.tool_operations.gateway import OperationGateway
from slim_guard.tool_operations.repository import ToolOperationRepository
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.policy import DefaultToolPolicy
from slim_guard.tools.registry import ToolRegistry


def build_tool_calls(
    *,
    database: Database,
    registry: ToolRegistry,
    state: HarnessStateRepository,
    business_executors: Mapping[str, ToolExecutor[Any]],
    memory_executors: Mapping[str, ToolExecutor[Any]],
    task_executors: Mapping[str, ToolExecutor[Any]],
    specialist_executors: Mapping[str, ToolExecutor[Any]],
    clock: Callable[[], datetime] | None,
) -> ToolCallCoordinator:
    gateway = OperationGateway(
        registry=registry,
        executors={
            **business_executors,
            **memory_executors,
            **task_executors,
            **specialist_executors,
        },
        execution_store=ToolExecutionRepository(database),
        operation_store=ToolOperationRepository(database, clock=clock),
        policy=DefaultToolPolicy(),
    )
    return ToolCallCoordinator(gateway=gateway, turn_state=state)
