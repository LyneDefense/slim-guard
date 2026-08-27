from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, ValidationError

from slim_guard.domain.routine.contracts import (
    RoutinePreferenceCommand,
    RoutinePreferenceRef,
    RoutineSetting,
)
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

CONFIGURE_ROUTINE_TOOL_NAME = "configure_checkin_schedule"
GET_ROUTINE_TOOL_NAME = "get_checkin_schedule"
ROUTINE_TOOL_VERSION = "v1"


class ConfigureRoutineArguments(ToolArguments):
    timezone: str | None = Field(default=None, description="IANA timezone, e.g. Asia/Shanghai")
    weight: RoutineSetting | None = None
    meal: RoutineSetting | None = None
    daily_review: RoutineSetting | None = None


class GetRoutineArguments(ToolArguments):
    pass


class RoutineToolHandlers:
    def __init__(self, repository: RoutinePreferenceRepository) -> None:
        self._repository = repository

    async def configure(
        self,
        context: ToolContext,
        arguments: ConfigureRoutineArguments,
    ) -> ToolResult:
        try:
            command = RoutinePreferenceCommand(
                user_id=context.user_id,
                **arguments.model_dump(),
            )
        except ValidationError:
            return ToolResult.failed(
                code="invalid_checkin_schedule",
                message=(
                    "The schedule needs a valid IANA timezone and HH:MM local times."
                ),
            )
        preference = await self._repository.update(command)
        return ToolResult.success(output=self._output(preference))

    async def get(
        self,
        context: ToolContext,
        arguments: GetRoutineArguments,
    ) -> ToolResult:
        preference = await self._repository.get(context.user_id)
        return ToolResult.success(
            output={"configured": preference is not None, **self._output(preference)}
            if preference is not None
            else {"configured": False}
        )

    @staticmethod
    def _output(preference: RoutinePreferenceRef) -> dict[str, Any]:
        return {
            "timezone": preference.timezone,
            "weight_reminder_time": preference.weight_reminder_time,
            "meal_reminder_time": preference.meal_reminder_time,
            "daily_review_time": preference.daily_review_time,
        }


def routine_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=CONFIGURE_ROUTINE_TOOL_NAME,
            description=(
                "Set, change, or disable the current user's local-time reminders for "
                "weight, meal check-ins, and daily review. Only call after the user "
                "explicitly requests the schedule change."
            ),
            version=ROUTINE_TOOL_VERSION,
            arguments_model=ConfigureRoutineArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_ROUTINE_TOOL_NAME,
            description="Read the current user's configured check-in and review schedule.",
            version=ROUTINE_TOOL_VERSION,
            arguments_model=GetRoutineArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def routine_tool_executors(
    repository: RoutinePreferenceRepository,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = RoutineToolHandlers(repository)
    return {
        CONFIGURE_ROUTINE_TOOL_NAME: ToolExecutor(
            arguments_model=ConfigureRoutineArguments,
            handler=handlers.configure,
        ),
        GET_ROUTINE_TOOL_NAME: ToolExecutor(
            arguments_model=GetRoutineArguments,
            handler=handlers.get,
        ),
    }
