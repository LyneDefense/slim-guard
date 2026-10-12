"""Model-facing task capability, independent of profile/long-term memory tools."""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError

from slim_guard.tasks.contracts import TaskConflict, TaskError, TaskWrite, Text
from slim_guard.tasks.ports import TaskStore
from slim_guard.tasks.presentation import task_summary
from slim_guard.tasks.record_requests import RecordRequest
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolEffectStatus,
    ToolMutationKind,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool


class SaveTaskArguments(ToolArguments):
    request: RecordRequest | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)
    objective: str = Field(min_length=1, max_length=300)
    unresolved: list[Text] = Field(max_length=20)
    status: Literal["open", "resolved", "cancelled"] = "open"
    evidence_excerpt: str = Field(max_length=512)
    interpretation: str = Field(min_length=1, max_length=1000)
    source_item_ids: list[str] = Field(default_factory=list, max_length=20)


class GetTaskArguments(ToolArguments):
    task_id: str = Field(min_length=1, max_length=128)
    evidence_offset: int = Field(default=0, ge=0)
    source_offset: int = Field(default=0, ge=0)


class ListTasksArguments(ToolArguments):
    status: Literal["open", "resolved", "cancelled", "expired"] = "open"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class TaskToolHandlers:
    def __init__(
        self, tasks: TaskStore, *, clock: Callable[[], datetime]
    ) -> None:
        self._tasks, self._clock = tasks, clock

    async def save(self, context: ToolContext, arguments: SaveTaskArguments) -> ToolResult:
        if context.source_item_id is None or context.execution_idempotency_key is None:
            return ToolResult.failed(
                code="task_source_missing",
                message="A user source is required.",
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        try:
            task = await self._tasks.write(
                TaskWrite(
                    **arguments.model_dump(),
                    user_id=context.user_id,
                    thread_id=context.thread_id,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    operation_id=context.execution_idempotency_key,
                )
            )
        except (TaskError, ValidationError) as exc:
            return ToolResult.failed(
                code="task_update_rejected",
                message=str(exc)[:1024],
                retryable=isinstance(exc, TaskConflict),
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        return ToolResult.success(output={"task": task_summary(task)}, source_ids=(task.id,))

    async def get(self, context: ToolContext, arguments: GetTaskArguments) -> ToolResult:
        try:
            task = await self._tasks.get(
                arguments.task_id, user_id=context.user_id, thread_id=context.thread_id
            )
        except TaskError as exc:
            return ToolResult.failed(code="task_not_found", message=str(exc))
        start = arguments.evidence_offset
        source_start = arguments.source_offset
        return ToolResult.success(
            output={
                "task": task_summary(task),
                "source_messages": await self._tasks.read_sources(task, offset=source_start),
                "attachment_ids": task.attachment_ids[source_start : source_start + 20],
                "source_next_offset": source_start + 20
                if source_start + 20 < max(len(task.source_item_ids), len(task.attachment_ids))
                else None,
                "evidence_page": [e.model_dump() for e in task.evidence[start : start + 20]],
                "evidence_next_offset": start + 20 if start + 20 < len(task.evidence) else None,
            },
            source_ids=(task.id,),
        )

    async def list(self, context: ToolContext, arguments: ListTasksArguments) -> ToolResult:
        tasks = await self._tasks.list_for_user(
            context.user_id,
            at=self._clock(),
            status=arguments.status,
            limit=arguments.limit,
            offset=arguments.offset,
        )
        return ToolResult.success(
            output={
                "tasks": [task_summary(task) for task in tasks],
                "next_offset": arguments.offset + len(tasks)
                if len(tasks) == arguments.limit
                else None,
            },
            source_ids=tuple(t.id for t in tasks),
        )

_TOOLS = (
    (
        "save_conversation_task",
        SaveTaskArguments,
        "save",
        ToolEffectLevel.REVERSIBLE_WRITE,
        "Create or update an unfinished cross-turn task when continuity is needed. Choose the "
        "remaining items and next steps from meaning, not fixed workflow stages. For updates use "
        "task_id and expected_revision; preserve earlier answers and remove resolved questions. "
        "Quote current user evidence exactly (empty only for image-only input); interpretation is "
        "your summary, not a user quote. Additional source_item_ids reference user messages or "
        "attachments. Multiple tasks coexist. Complete record requests through write receipts.",
    ),
    (
        "get_conversation_task",
        GetTaskArguments,
        "get",
        ToolEffectLevel.READ,
        "Load a task, original attachment references and paginated answer evidence. "
        "Historical visual uncertainty does not override later user answers.",
    ),
    (
        "list_conversation_tasks",
        ListTasksArguments,
        "list",
        ToolEffectLevel.READ,
        "List the user's tasks with pagination, including closed tasks when requested.",
    ),
)


def task_tool_definitions() -> tuple[RegisteredTool, ...]:
    return tuple(
        RegisteredTool(
            name=name,
            description=description,
            version="v1",
            arguments_model=model,
            effect_level=effect,
            mutation_kind=(
                ToolMutationKind.CONVERSATION_TASK
                if name == "save_conversation_task"
                else ToolMutationKind.NONE
            ),
            idempotent=True,
            timeout_seconds=5,
        )
        for name, model, _, effect, description in _TOOLS
    )


def task_tool_executors(handlers: TaskToolHandlers) -> Mapping[str, ToolExecutor[Any]]:
    return {
        name: ToolExecutor(arguments_model=model, handler=getattr(handlers, method))
        for name, model, method, _, _ in _TOOLS
    }
