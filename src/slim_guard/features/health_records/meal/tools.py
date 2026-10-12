from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from slim_guard.features.health_records.meal.contracts import (
    MealFood,
    MealRecordCommand,
    MealRecordRef,
    MealType,
)
from slim_guard.features.health_records.meal.errors import MealRecordCollision, MealSourceMismatch
from slim_guard.features.health_records.ports import MealStore
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

RECORD_MEAL_TOOL_NAME = "record_meal"
GET_RECENT_MEALS_TOOL_NAME = "get_recent_meals"
MEAL_TOOL_VERSION = "v3"


class MealFoodArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=128)
    portion: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("name", "portion")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Meal food text cannot be blank")
        return normalized


class RecordMealArguments(ToolArguments):
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = "unspecified"
    # Model tool calls arrive as JSON, whose collection type is an array/list.
    # Keep the boundary JSON-native and normalize to immutable tuples only in
    # the trusted domain command below.
    foods: list[MealFoodArguments] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: str | None = None


class GetRecentMealsArguments(ToolArguments):
    limit: int = Field(default=10, ge=1, le=31)


class MealToolHandlers:
    def __init__(
        self,
        repository: MealStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or self._utc_now

    async def lookup_result(self, context: ToolContext) -> ToolResult | None:
        if context.execution_idempotency_key is None:
            return None
        record = await self._repository.by_operation(
            context.user_id, context.execution_idempotency_key
        )
        if record is None:
            return None
        return ToolResult.success(
            output={**self._record_output(record), "created": False},
            source_ids=(record.id,),
        )

    async def record_meal(
        self,
        context: ToolContext,
        arguments: RecordMealArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None:
            return ToolResult.failed(
                effect_status=ToolEffectStatus.NOT_APPLIED,
                code="missing_execution_identity",
                message="The meal record is missing its trusted execution identity.",
            )
        try:
            command = MealRecordCommand(
                user_id=context.user_id,
                meal_type=MealType(arguments.meal_type),
                foods=tuple(
                    MealFood(name=food.name, portion=food.portion) for food in arguments.foods
                ),
                note=arguments.note,
                occurred_at=self._occurrence_time(
                    arguments.occurred_at
                    or (
                        context.operation_started_at.isoformat()
                        if context.operation_started_at
                        else None
                    )
                ),
                idempotency_key=context.execution_idempotency_key,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                source_tool_call_id=context.tool_call_id,
            )
        except ValidationError:
            return ToolResult.failed(
                effect_status=ToolEffectStatus.NOT_APPLIED,
                code="invalid_meal_record",
                message="The meal record contains invalid food or note data.",
            )
        except ValueError:
            return ToolResult.failed(
                effect_status=ToolEffectStatus.NOT_APPLIED,
                code="invalid_meal_time",
                message="The meal time must be a timezone-aware ISO 8601 value.",
            )
        try:
            creation = await self._repository.record(command)
        except MealRecordCollision:
            return ToolResult.failed(
                effect_status=ToolEffectStatus.NOT_APPLIED,
                code="meal_record_collision",
                message="This meal operation conflicts with an existing record.",
            )
        except MealSourceMismatch:
            return ToolResult.failed(
                effect_status=ToolEffectStatus.NOT_APPLIED,
                code="meal_source_mismatch",
                message="The meal record source could not be verified.",
            )
        return ToolResult.success(
            output={**self._record_output(creation.record), "created": creation.created},
            source_ids=(creation.record.id,),
        )

    async def get_recent_meals(
        self,
        context: ToolContext,
        arguments: GetRecentMealsArguments,
    ) -> ToolResult:
        records = await self._repository.recent(context.user_id, limit=arguments.limit)
        return ToolResult.success(
            output={"records": [self._record_output(record) for record in records]},
            source_ids=tuple(record.id for record in records),
        )

    def _occurrence_time(self, raw: str | None) -> datetime:
        value = self._clock() if raw is None else self._parse_datetime(raw)
        if value.utcoffset() is None:
            raise ValueError("Meal time must be timezone-aware")
        return value

    @staticmethod
    def _parse_datetime(raw: str) -> datetime:
        normalized = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
        return datetime.fromisoformat(normalized)

    @staticmethod
    def _record_output(record: MealRecordRef) -> dict[str, Any]:
        return {
            "record_id": record.id,
            "meal_type": record.meal_type.value,
            "foods": [food.model_dump(mode="json") for food in record.foods],
            "note": record.note,
            "occurred_at": record.occurred_at.isoformat() if record.occurred_at else None,
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def meal_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RECORD_MEAL_TOOL_NAME,
            description=(
                "Persist foods supported by user statements or reliable observations. Use "
                "conversation task evidence for earlier answers; do not treat historical "
                "visual uncertainty as a new unanswered question. Decide whether missing "
                "details matter to this record before asking. Never invent foods or numbers."
            ),
            version=MEAL_TOOL_VERSION,
            arguments_model=RecordMealArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            mutation_kind=ToolMutationKind.HEALTH_RECORD,
            idempotent=True,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_RECENT_MEALS_TOOL_NAME,
            description="Read the current user's recent authoritative meal records.",
            version=MEAL_TOOL_VERSION,
            arguments_model=GetRecentMealsArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            timeout_seconds=3,
        ),
    )


def meal_tool_executors(
    repository: MealStore,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = MealToolHandlers(repository, clock=clock)
    return {
        RECORD_MEAL_TOOL_NAME: ToolExecutor(
            arguments_model=RecordMealArguments,
            handler=handlers.record_meal,
            lookup_result=handlers.lookup_result,
        ),
        GET_RECENT_MEALS_TOOL_NAME: ToolExecutor(
            arguments_model=GetRecentMealsArguments,
            handler=handlers.get_recent_meals,
        ),
    }
