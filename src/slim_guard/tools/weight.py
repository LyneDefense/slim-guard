from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError

from slim_guard.domain.weight.contracts import (
    WeightMeasurementCommand,
    WeightMeasurementCondition,
    WeightRecordRef,
    WeightTrend,
    WeightUnit,
)
from slim_guard.domain.weight.errors import WeightRecordCollision, WeightSourceMismatch
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolResult,
)
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

RECORD_WEIGHT_TOOL_NAME = "record_weight"
GET_RECENT_WEIGHT_TREND_TOOL_NAME = "get_recent_weight_trend"
WEIGHT_TOOL_VERSION = "v1"


class RecordWeightArguments(ToolArguments):
    """Only measurement facts may be supplied by the model."""

    value: float = Field(gt=0)
    unit: Literal["kg", "jin", "lb"] = "kg"
    condition: Literal["fasting", "post_meal", "unspecified"] = "unspecified"
    measured_at: str | None = None


class GetRecentWeightTrendArguments(ToolArguments):
    limit: int = Field(default=7, ge=1, le=31)


class WeightToolHandlers:
    """Bridges trusted Harness calls to the authoritative weight domain."""

    def __init__(
        self,
        repository: WeightRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or self._utc_now

    async def record_weight(
        self,
        context: ToolContext,
        arguments: RecordWeightArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None:
            return ToolResult.failed(
                code="missing_execution_identity",
                message="The weight record is missing its trusted execution identity.",
            )
        try:
            measured_at = self._measurement_time(arguments.measured_at)
            command = WeightMeasurementCommand(
                user_id=context.user_id,
                value=Decimal(str(arguments.value)),
                unit=WeightUnit(arguments.unit),
                measured_at=measured_at,
                condition=WeightMeasurementCondition(arguments.condition),
                idempotency_key=context.execution_idempotency_key,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                source_tool_call_id=context.tool_call_id,
            )
            creation = await self._repository.record(command)
        except ValidationError:
            return ToolResult.failed(
                code="invalid_weight_measurement",
                message="The weight measurement is invalid or outside the supported range.",
            )
        except ValueError:
            return ToolResult.failed(
                code="invalid_measurement_time",
                message="The measurement time must be a timezone-aware ISO 8601 value.",
            )
        except WeightRecordCollision:
            return ToolResult.failed(
                code="weight_record_collision",
                message="This weight operation conflicts with an existing record.",
            )
        except WeightSourceMismatch:
            return ToolResult.failed(
                code="weight_source_mismatch",
                message="The weight record source could not be verified.",
            )

        return ToolResult.success(
            output={
                **self._record_output(creation.record),
                "created": creation.created,
            },
            source_ids=(creation.record.id,),
        )

    async def get_recent_weight_trend(
        self,
        context: ToolContext,
        arguments: GetRecentWeightTrendArguments,
    ) -> ToolResult:
        trend = await self._repository.recent_trend(
            context.user_id,
            limit=arguments.limit,
        )
        return ToolResult.success(
            output=self._trend_output(trend),
            source_ids=tuple(record.id for record in trend.records),
        )

    def _measurement_time(self, raw: str | None) -> datetime:
        measured_at = self._clock() if raw is None else self._parse_datetime(raw)
        if measured_at.utcoffset() is None:
            raise ValueError("Measurement time must be timezone-aware")
        return measured_at

    @staticmethod
    def _parse_datetime(raw: str) -> datetime:
        normalized = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
        return datetime.fromisoformat(normalized)

    @classmethod
    def _trend_output(cls, trend: WeightTrend) -> dict[str, Any]:
        return {
            "records": [cls._record_output(record) for record in trend.records],
            "current_record_id": trend.current.id if trend.current else None,
            "previous_record_id": trend.previous.id if trend.previous else None,
            "change_kg": cls._decimal_text(trend.change_kg),
            "direction": trend.direction.value,
        }

    @staticmethod
    def _record_output(record: WeightRecordRef) -> dict[str, Any]:
        return {
            "record_id": record.id,
            "weight_kg": format(record.weight_kg, "f"),
            "weight_grams": record.weight_grams,
            "original_value": record.original_value,
            "original_unit": record.original_unit.value,
            "measured_at": record.measured_at.isoformat(),
            "condition": record.condition.value,
        }

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def weight_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RECORD_WEIGHT_TOOL_NAME,
            description=(
                "Persist one weight measurement explicitly supplied by the current user. "
                "Do not infer a weight that the user did not state or show. Use measured_at "
                "only when the message provides a reliable measurement time."
            ),
            version=WEIGHT_TOOL_VERSION,
            arguments_model=RecordWeightArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_RECENT_WEIGHT_TREND_TOOL_NAME,
            description=(
                "Read the current user's recent authoritative weight measurements and "
                "their deterministic change from the previous measurement."
            ),
            version=WEIGHT_TOOL_VERSION,
            arguments_model=GetRecentWeightTrendArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def weight_tool_executors(
    repository: WeightRepository,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = WeightToolHandlers(repository, clock=clock)
    return {
        RECORD_WEIGHT_TOOL_NAME: ToolExecutor(
            arguments_model=RecordWeightArguments,
            handler=handlers.record_weight,
        ),
        GET_RECENT_WEIGHT_TREND_TOOL_NAME: ToolExecutor(
            arguments_model=GetRecentWeightTrendArguments,
            handler=handlers.get_recent_weight_trend,
        ),
    }
