"""Own task continuity and connect it to existing operation authorizations."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.tasks.config import TaskRuntimeConfig
from slim_guard.tasks.ports import TaskStore
from slim_guard.tasks.repository import ConversationTaskRepository
from slim_guard.tasks.tools import TaskToolHandlers, task_tool_executors
from slim_guard.tools.gateway import ToolExecutor


@dataclass(frozen=True, slots=True)
class TaskComponents:
    store: TaskStore
    executors: Mapping[str, ToolExecutor[Any]]


def build_task_components(
    *,
    database: Database,
    config: TaskRuntimeConfig,
    clock: Callable[[], datetime] | None,
) -> TaskComponents:
    active_clock = clock or utc_now
    tasks = ConversationTaskRepository(
        database,
        ttl=timedelta(days=config.clarification_ttl_days),
        clock=active_clock,
    )
    return TaskComponents(
        store=tasks,
        executors=task_tool_executors(TaskToolHandlers(tasks, clock=active_clock)),
    )
