from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import desc, select

from slim_guard.db.models import AgentItemRecord
from slim_guard.domain.meal.contracts import MealFood, MealRecordCommand, MealRecordRef, MealType
from slim_guard.domain.meal.errors import MealRecordCollision, MealSourceMismatch
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

RECORD_MEAL_TOOL_NAME = "record_meal"
GET_RECENT_MEALS_TOOL_NAME = "get_recent_meals"
MEAL_TOOL_VERSION = "v2"


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
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = (
        "unspecified"
    )
    # Model tool calls arrive as JSON, whose collection type is an array/list.
    # Keep the boundary JSON-native and normalize to immutable tuples only in
    # the trusted domain command below.
    foods: list[MealFoodArguments] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: str | None = None
    visual_confirmation: Literal[
        "not_confirmed", "confirmed_by_current_user"
    ] = "not_confirmed"


class GetRecentMealsArguments(ToolArguments):
    limit: int = Field(default=10, ge=1, le=31)


class MealToolHandlers:
    def __init__(
        self,
        repository: MealRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or self._utc_now

    async def record_meal(
        self,
        context: ToolContext,
        arguments: RecordMealArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None:
            return ToolResult.failed(
                code="missing_execution_identity",
                message="The meal record is missing its trusted execution identity.",
            )
        visual_failure = await self._visual_confirmation_failure(context, arguments)
        if visual_failure is not None:
            return visual_failure
        try:
            command = MealRecordCommand(
                user_id=context.user_id,
                meal_type=MealType(arguments.meal_type),
                foods=tuple(
                    MealFood(name=food.name, portion=food.portion)
                    for food in arguments.foods
                ),
                note=arguments.note,
                occurred_at=self._occurrence_time(arguments.occurred_at),
                idempotency_key=context.execution_idempotency_key,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                source_tool_call_id=context.tool_call_id,
            )
            creation = await self._repository.record(command)
        except ValidationError:
            return ToolResult.failed(
                code="invalid_meal_record",
                message="The meal record contains invalid food or note data.",
            )
        except ValueError:
            return ToolResult.failed(
                code="invalid_meal_time",
                message="The meal time must be a timezone-aware ISO 8601 value.",
            )
        except MealRecordCollision:
            return ToolResult.failed(
                code="meal_record_collision",
                message="This meal operation conflicts with an existing record.",
            )
        except MealSourceMismatch:
            return ToolResult.failed(
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

    async def _visual_confirmation_failure(
        self,
        context: ToolContext,
        arguments: RecordMealArguments,
    ) -> ToolResult | None:
        """Enforce model-authored uncertainty without interpreting user language."""
        async with self._repository.database.session() as session:
            # A clarification answer is normally sent in a new Turn.  The
            # inspect_image result therefore cannot be limited to the current
            # turn; use the latest completed observations in this thread and
            # let the current user message resolve the structured candidates.
            result_items = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .where(
                        AgentItemRecord.thread_id == context.thread_id,
                        AgentItemRecord.item_type == "tool_result",
                        AgentItemRecord.status == "completed",
                    )
                    .order_by(desc(AgentItemRecord.created_at), desc(AgentItemRecord.sequence))
                    .limit(50)
                )
            )
            latest_inspection = next(
                (
                    item
                    for item in result_items
                    if self._is_inspection_result(item)
                ),
                None,
            )
            if latest_inspection is None:
                return None
            result_items = (latest_inspection,)
            requires_confirmation = self._inspection_requires_confirmation(latest_inspection)
            if not requires_confirmation:
                return None
            if arguments.visual_confirmation != "confirmed_by_current_user":
                return ToolResult.failed(
                    code="visual_confirmation_required",
                    message=(
                        "The vision model found unresolved ambiguity. Ask the user to "
                        "confirm it before recording this meal."
                    ),
                )
            source = (
                await session.get(AgentItemRecord, context.source_item_id)
                if context.source_item_id is not None
                else None
            )
            if (
                source is None
                or source.turn_id != context.turn_id
                or source.item_type != "user_message"
                or source.status != "completed"
            ):
                return ToolResult.failed(
                    code="visual_confirmation_source_missing",
                    message=(
                        "A model decision to treat visual ambiguity as confirmed requires "
                        "a current user message as evidence."
                    ),
                )
            unresolved = self._unresolved_dish_choices(result_items, source)
            if unresolved:
                return ToolResult.failed(
                    code="visual_confirmation_required",
                    message=(
                        "请直接回答仍有歧义的菜名后再记录："
                        + "；".join(unresolved)
                    ),
                )
        return None

    @staticmethod
    def _is_inspection_result(item: AgentItemRecord) -> bool:
        try:
            payload = json.loads(item.payload_json)
            execution = payload.get("execution", {})
        except (AttributeError, TypeError, json.JSONDecodeError):
            return False
        return execution.get("tool_name") == "inspect_image"

    @staticmethod
    def _inspection_requires_confirmation(item: AgentItemRecord) -> bool:
        try:
            payload = json.loads(item.payload_json)
            execution = payload.get("execution", {})
            result = execution.get("result", {})
            output = result.get("output", {})
        except (AttributeError, TypeError, json.JSONDecodeError):
            return False
        return (
            execution.get("tool_name") == "inspect_image"
            and result.get("status") == "succeeded"
            and output.get("requires_user_confirmation") is True
        )

    @staticmethod
    def _unresolved_dish_choices(
        items: tuple[AgentItemRecord, ...],
        source: AgentItemRecord | None,
    ) -> list[str]:
        """Require the current user message to name each unresolved choice.

        A generic acknowledgement such as “确认” cannot prove which side of a
        visual choice the user selected. This deterministic guard prevents the
        model from turning that acknowledgement into a false dish fact.
        """

        if source is None:
            return []
        try:
            source_payload = json.loads(source.payload_json)
            user_text = str(source_payload.get("text", "")).strip()
        except (AttributeError, TypeError, json.JSONDecodeError):
            user_text = ""
        unresolved: list[str] = []
        for item in items:
            try:
                payload = json.loads(item.payload_json)
                execution = payload.get("execution", {})
                if execution.get("tool_name") != "inspect_image":
                    continue
                result = execution.get("result", {})
                output = result.get("output", {})
                recognition = output.get("dish_recognition", {})
                dishes = recognition.get("dishes", [])
            except (AttributeError, TypeError, json.JSONDecodeError):
                continue
            if not isinstance(dishes, list):
                continue
            for dish in dishes:
                if not isinstance(dish, dict) or dish.get("requires_confirmation") is not True:
                    continue
                candidates = dish.get("candidates", [])
                labels = [
                    candidate.get("label", "").strip()
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("label"), str)
                    and candidate.get("label", "").strip()
                ]
                if labels and not any(label in user_text for label in labels):
                    unresolved.append(" / ".join(labels))
        return list(dict.fromkeys(unresolved))

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
            "occurred_at": record.occurred_at.isoformat(),
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def meal_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RECORD_MEAL_TOOL_NAME,
            description=(
                "Persist foods explicitly reported by the current user or clearly observed "
                "through inspect_image. If inspect_image requires user confirmation, do not "
                "call this tool until the current user message semantically resolves the "
                "ambiguity; then set visual_confirmation=confirmed_by_current_user. "
                "Never invent calories or foods that were not reported or clearly visible."
            ),
            version=MEAL_TOOL_VERSION,
            arguments_model=RecordMealArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_RECENT_MEALS_TOOL_NAME,
            description="Read the current user's recent authoritative meal records.",
            version=MEAL_TOOL_VERSION,
            arguments_model=GetRecentMealsArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def meal_tool_executors(
    repository: MealRepository,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = MealToolHandlers(repository, clock=clock)
    return {
        RECORD_MEAL_TOOL_NAME: ToolExecutor(
            arguments_model=RecordMealArguments,
            handler=handlers.record_meal,
        ),
        GET_RECENT_MEALS_TOOL_NAME: ToolExecutor(
            arguments_model=GetRecentMealsArguments,
            handler=handlers.get_recent_meals,
        ),
    }
