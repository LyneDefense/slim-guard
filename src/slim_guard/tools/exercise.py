from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from slim_guard.domain.exercise.contracts import ExerciseRecordCommand, ExerciseRecordRef
from slim_guard.domain.exercise.errors import (
    ExerciseRecordCollision,
    ExerciseSourceMismatch,
)
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

RECORD_EXERCISE_TOOL_NAME = "record_exercise"
GET_RECENT_EXERCISE_TOOL_NAME = "get_recent_exercise"
EXERCISE_TOOL_VERSION = "v1"

_METERS_PER_UNIT = {
    "m": Decimal("1"),
    "km": Decimal("1000"),
    "mile": Decimal("1609.344"),
}


class RecordExerciseArguments(ToolArguments):
    activity_name: str = Field(min_length=1, max_length=128)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    steps: int | None = Field(default=None, ge=0, le=200_000)
    distance_value: float | None = Field(default=None, ge=0)
    distance_unit: Literal["m", "km", "mile"] = "km"
    reported_energy_kcal: int | None = Field(default=None, ge=0, le=20_000)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: str | None = None

    @field_validator("activity_name", "note")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Exercise text cannot be blank")
        return normalized


class GetRecentExerciseArguments(ToolArguments):
    limit: int = Field(default=10, ge=1, le=31)


class ExerciseToolHandlers:
    def __init__(
        self,
        repository: ExerciseRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or self._utc_now

    async def record_exercise(
        self,
        context: ToolContext,
        arguments: RecordExerciseArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None:
            return ToolResult.failed(
                code="missing_execution_identity",
                message="The exercise record is missing its trusted execution identity.",
            )
        try:
            command = ExerciseRecordCommand(
                user_id=context.user_id,
                activity_name=arguments.activity_name,
                duration_minutes=arguments.duration_minutes,
                steps=arguments.steps,
                distance_meters=self._distance_meters(
                    arguments.distance_value,
                    arguments.distance_unit,
                ),
                reported_energy_kcal=arguments.reported_energy_kcal,
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
                code="invalid_exercise_record",
                message="The exercise record contains invalid or out-of-range data.",
            )
        except ValueError:
            return ToolResult.failed(
                code="invalid_exercise_time",
                message="The exercise time must be a timezone-aware ISO 8601 value.",
            )
        except ExerciseRecordCollision:
            return ToolResult.failed(
                code="exercise_record_collision",
                message="This exercise operation conflicts with an existing record.",
            )
        except ExerciseSourceMismatch:
            return ToolResult.failed(
                code="exercise_source_mismatch",
                message="The exercise record source could not be verified.",
            )
        return ToolResult.success(
            output={**self._record_output(creation.record), "created": creation.created},
            source_ids=(creation.record.id,),
        )

    async def get_recent_exercise(
        self,
        context: ToolContext,
        arguments: GetRecentExerciseArguments,
    ) -> ToolResult:
        records = await self._repository.recent(context.user_id, limit=arguments.limit)
        return ToolResult.success(
            output={"records": [self._record_output(record) for record in records]},
            source_ids=tuple(record.id for record in records),
        )

    def _occurrence_time(self, raw: str | None) -> datetime:
        value = self._clock() if raw is None else self._parse_datetime(raw)
        if value.utcoffset() is None:
            raise ValueError("Exercise time must be timezone-aware")
        return value

    @staticmethod
    def _parse_datetime(raw: str) -> datetime:
        normalized = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
        return datetime.fromisoformat(normalized)

    @staticmethod
    def _distance_meters(value: float | None, unit: str) -> int | None:
        if value is None:
            return None
        meters = Decimal(str(value)) * _METERS_PER_UNIT[unit]
        return int(meters.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _record_output(record: ExerciseRecordRef) -> dict[str, Any]:
        return {
            "record_id": record.id,
            "activity_name": record.activity_name,
            "duration_minutes": record.duration_minutes,
            "steps": record.steps,
            "distance_meters": record.distance_meters,
            "reported_energy_kcal": record.reported_energy_kcal,
            "note": record.note,
            "occurred_at": record.occurred_at.isoformat(),
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def exercise_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RECORD_EXERCISE_TOOL_NAME,
            description=(
                "Persist one exercise activity explicitly reported by the current user or "
                "clearly observed through inspect_image. Keep activity_name open-ended. "
                "Only include metrics explicitly reported by the user or device."
            ),
            version=EXERCISE_TOOL_VERSION,
            arguments_model=RecordExerciseArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_RECENT_EXERCISE_TOOL_NAME,
            description="Read the current user's recent authoritative exercise records.",
            version=EXERCISE_TOOL_VERSION,
            arguments_model=GetRecentExerciseArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def exercise_tool_executors(
    repository: ExerciseRepository,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = ExerciseToolHandlers(repository, clock=clock)
    return {
        RECORD_EXERCISE_TOOL_NAME: ToolExecutor(
            arguments_model=RecordExerciseArguments,
            handler=handlers.record_exercise,
        ),
        GET_RECENT_EXERCISE_TOOL_NAME: ToolExecutor(
            arguments_model=GetRecentExerciseArguments,
            handler=handlers.get_recent_exercise,
        ),
    }
