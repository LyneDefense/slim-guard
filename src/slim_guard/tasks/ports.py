from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from slim_guard.tasks.contracts import TaskRef, TaskStatus, TaskWrite


class TaskStore(Protocol):
    async def write(self, command: TaskWrite) -> TaskRef: ...

    async def get(self, task_id: str, *, user_id: str, thread_id: str) -> TaskRef: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        at: datetime,
        status: TaskStatus = "open",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[TaskRef, ...]: ...

    async def expire_due(self, *, at: datetime) -> int: ...

    async def read_sources(self, task: TaskRef, *, offset: int) -> tuple[dict[str, Any], ...]: ...
