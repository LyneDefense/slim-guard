"""Wire tool execution, approval and resume using one shared registry."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from slim_guard.db.session import Database
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.pending_config import PendingActionConfig
from slim_guard.harness.pending_resume import PendingActionResumeCoordinator
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolExecutor, ToolGateway
from slim_guard.tools.pending import PendingActionToolHandlers, pending_action_tool_executors
from slim_guard.tools.policy import DefaultToolPolicy
from slim_guard.tools.registry import ToolRegistry


def build_tool_calls(
    *,
    database: Database,
    registry: ToolRegistry,
    state: HarnessStateRepository,
    pending_actions: PendingActionRepository,
    business_executors: Mapping[str, ToolExecutor[Any]],
    memory_executors: Mapping[str, ToolExecutor[Any]],
    task_executors: Mapping[str, ToolExecutor[Any]],
    specialist_executors: Mapping[str, ToolExecutor[Any]],
    config: PendingActionConfig,
    clock: Callable[[], datetime] | None,
) -> ToolCallCoordinator:
    pending_handlers = PendingActionToolHandlers(
        pending_actions=pending_actions,
        state=state,
        clock=clock,
    )
    gateway = ToolGateway(
        registry=registry,
        executors={
            **business_executors,
            **memory_executors,
            **task_executors,
            **pending_action_tool_executors(pending_handlers),
            **specialist_executors,
        },
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    tool_calls = ToolCallCoordinator(
        gateway=gateway,
        pending_actions=pending_actions,
        turn_state=state,
        confirmation_ttl=timedelta(seconds=config.confirmation_ttl_seconds),
        review_ttl=timedelta(seconds=config.review_ttl_seconds),
    )
    pending_handlers.bind(
        PendingActionResumeCoordinator(
            pending_actions=pending_actions,
            turn_state=state,
            tool_calls=tool_calls,
        )
    )
    return tool_calls
