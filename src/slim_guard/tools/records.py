from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from slim_guard.domain.records.service import (
    RecordKind,
    RecordStatusAction,
    UserRecordStatusService,
)
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

UPDATE_RECORD_STATUS_TOOL_NAME = "update_record_status"
RECORD_STATUS_TOOL_VERSION = "v1"


class UpdateRecordStatusArguments(ToolArguments):
    record_kind: Literal["weight", "body_fat", "meal", "exercise"]
    record_id: str
    action: Literal["void", "restore"]


class RecordStatusToolHandlers:
    def __init__(self, service: UserRecordStatusService) -> None:
        self._service = service

    async def update(
        self,
        context: ToolContext,
        arguments: UpdateRecordStatusArguments,
    ) -> ToolResult:
        try:
            result = await self._service.apply(
                user_id=context.user_id,
                record_kind=RecordKind(arguments.record_kind),
                record_id=arguments.record_id,
                action=RecordStatusAction(arguments.action),
            )
        except ValueError:
            return ToolResult.failed(
                code="record_status_conflict",
                message="This record cannot be changed from its current status.",
            )
        if result is None:
            return ToolResult.failed(
                code="record_not_found",
                message="No matching record owned by the current user was found.",
            )
        return ToolResult.success(
            output={
                "record_id": result.record_id,
                "record_kind": result.record_kind.value,
                "status": result.status,
                "changed": result.changed,
            },
            source_ids=(result.record_id,),
        )


def record_status_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=UPDATE_RECORD_STATUS_TOOL_NAME,
            description=(
                "Void a mistaken weight, body-fat, meal, or exercise record owned by the current "
                "user, or restore a previously voided record. First use a recent-record "
                "read tool to obtain the exact record_id. Never guess an ID."
            ),
            version=RECORD_STATUS_TOOL_VERSION,
            arguments_model=UpdateRecordStatusArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def record_status_tool_executors(
    service: UserRecordStatusService,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = RecordStatusToolHandlers(service)
    return {
        UPDATE_RECORD_STATUS_TOOL_NAME: ToolExecutor(
            arguments_model=UpdateRecordStatusArguments,
            handler=handlers.update,
        )
    }
