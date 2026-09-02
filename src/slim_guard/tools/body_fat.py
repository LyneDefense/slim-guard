from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, ValidationError

from slim_guard.domain.body_fat.contracts import (
    BodyFatMeasurementCommand,
    BodyFatRecordRef,
    BodyFatTrend,
)
from slim_guard.domain.body_fat.errors import (
    BodyFatRecordCollision,
    BodyFatSourceMismatch,
)
from slim_guard.domain.body_fat.repository import BodyFatRepository
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

RECORD_BODY_FAT_TOOL_NAME = "record_body_fat"
GET_RECENT_BODY_FAT_TREND_TOOL_NAME = "get_recent_body_fat_trend"
BODY_FAT_TOOL_VERSION = "v1"


class RecordBodyFatArguments(ToolArguments):
    value: float = Field(gt=0)
    measured_at: str | None = None


class GetRecentBodyFatTrendArguments(ToolArguments):
    limit: int = Field(default=7, ge=1, le=31)


class BodyFatToolHandlers:
    def __init__(
        self,
        repository: BodyFatRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or self._utc_now

    async def record_body_fat(
        self,
        context: ToolContext,
        arguments: RecordBodyFatArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None:
            return ToolResult.failed(
                code="missing_execution_identity",
                message="The body-fat record is missing its trusted execution identity.",
            )
        try:
            measured_at = self._measurement_time(arguments.measured_at)
            creation = await self._repository.record(
                BodyFatMeasurementCommand(
                    user_id=context.user_id,
                    value=Decimal(str(arguments.value)),
                    measured_at=measured_at,
                    idempotency_key=context.execution_idempotency_key,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    source_tool_call_id=context.tool_call_id,
                )
            )
        except ValidationError:
            return ToolResult.failed(
                code="invalid_body_fat_measurement",
                message="The body-fat percentage is invalid or outside the supported range.",
            )
        except ValueError:
            return ToolResult.failed(
                code="invalid_measurement_time",
                message="The measurement time must be a timezone-aware ISO 8601 value.",
            )
        except BodyFatRecordCollision:
            return ToolResult.failed(
                code="body_fat_record_collision",
                message="This body-fat operation conflicts with an existing record.",
            )
        except BodyFatSourceMismatch:
            return ToolResult.failed(
                code="body_fat_source_mismatch",
                message="The body-fat record source could not be verified.",
            )
        return ToolResult.success(
            output={**self._record_output(creation.record), "created": creation.created},
            source_ids=(creation.record.id,),
        )

    async def get_recent_body_fat_trend(
        self,
        context: ToolContext,
        arguments: GetRecentBodyFatTrendArguments,
    ) -> ToolResult:
        trend = await self._repository.recent_trend(context.user_id, limit=arguments.limit)
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
    def _trend_output(cls, trend: BodyFatTrend) -> dict[str, Any]:
        return {
            "records": [cls._record_output(record) for record in trend.records],
            "current_record_id": trend.current.id if trend.current else None,
            "previous_record_id": trend.previous.id if trend.previous else None,
            "change_percentage_points": cls._decimal_text(
                trend.change_percentage_points
            ),
            "direction": trend.direction.value,
        }

    @staticmethod
    def _record_output(record: BodyFatRecordRef) -> dict[str, Any]:
        return {
            "record_id": record.id,
            "body_fat_percent": format(record.percent, "f"),
            "body_fat_basis_points": record.basis_points,
            "original_value": record.original_value,
            "measured_at": record.measured_at.isoformat(),
        }

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def body_fat_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RECORD_BODY_FAT_TOOL_NAME,
            description=(
                "Persist one body-fat percentage explicitly supplied by the current user. "
                "The model decides which number is the current measurement. Percent is the "
                "only unit, so a value such as '体脂31' means 31%. Do not confuse it with a "
                "target body-fat percentage."
            ),
            version=BODY_FAT_TOOL_VERSION,
            arguments_model=RecordBodyFatArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=GET_RECENT_BODY_FAT_TREND_TOOL_NAME,
            description=(
                "Read the current user's recent authoritative body-fat measurements and "
                "their deterministic change in percentage points."
            ),
            version=BODY_FAT_TOOL_VERSION,
            arguments_model=GetRecentBodyFatTrendArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
    )


def body_fat_tool_executors(
    repository: BodyFatRepository,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = BodyFatToolHandlers(repository, clock=clock)
    return {
        RECORD_BODY_FAT_TOOL_NAME: ToolExecutor(
            arguments_model=RecordBodyFatArguments,
            handler=handlers.record_body_fat,
        ),
        GET_RECENT_BODY_FAT_TREND_TOOL_NAME: ToolExecutor(
            arguments_model=GetRecentBodyFatTrendArguments,
            handler=handlers.get_recent_body_fat_trend,
        ),
    }
