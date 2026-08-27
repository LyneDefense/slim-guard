from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_GRAMS_PER_UNIT = {
    "kg": Decimal("1000"),
    "jin": Decimal("500"),
    "lb": Decimal("453.59237"),
}
_MIN_WEIGHT_GRAMS = 10_000
_MAX_WEIGHT_GRAMS = 500_000


class WeightUnit(StrEnum):
    KG = "kg"
    JIN = "jin"
    LB = "lb"


class WeightMeasurementCondition(StrEnum):
    FASTING = "fasting"
    POST_MEAL = "post_meal"
    UNSPECIFIED = "unspecified"


class WeightRecordStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    VOIDED = "voided"


class WeightTrendDirection(StrEnum):
    DOWN = "down"
    STABLE = "stable"
    UP = "up"
    UNKNOWN = "unknown"


class WeightMeasurementCommand(BaseModel):
    """Validated canonical command accepted by the weight domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    value: Decimal = Field(gt=0)
    unit: WeightUnit
    measured_at: datetime
    condition: WeightMeasurementCondition = WeightMeasurementCondition.UNSPECIFIED
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        if self.measured_at.utcoffset() is None:
            raise ValueError("Weight measurement time must be timezone-aware")
        if not _MIN_WEIGHT_GRAMS <= self.weight_grams <= _MAX_WEIGHT_GRAMS:
            raise ValueError("Weight measurement must be between 10kg and 500kg")
        return self

    @property
    def weight_grams(self) -> int:
        grams = self.value * _GRAMS_PER_UNIT[self.unit.value]
        return int(grams.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @property
    def canonical_original_value(self) -> str:
        return format(self.value.normalize(), "f")

    @property
    def measured_at_utc(self) -> datetime:
        return self.measured_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class WeightRecordRef:
    id: str
    user_id: str
    weight_grams: int
    original_value: str
    original_unit: WeightUnit
    measured_at: datetime
    condition: WeightMeasurementCondition
    status: WeightRecordStatus
    idempotency_key: str
    source_turn_id: str
    source_item_id: str | None
    source_tool_call_id: str
    supersedes_id: str | None
    created_at: datetime
    superseded_at: datetime | None

    @property
    def weight_kg(self) -> Decimal:
        return Decimal(self.weight_grams) / Decimal("1000")


@dataclass(frozen=True, slots=True)
class WeightRecordCreation:
    record: WeightRecordRef
    created: bool


@dataclass(frozen=True, slots=True)
class WeightTrend:
    records: tuple[WeightRecordRef, ...]
    current: WeightRecordRef | None
    previous: WeightRecordRef | None
    change_kg: Decimal | None
    direction: WeightTrendDirection

    @classmethod
    def from_records(cls, records: tuple[WeightRecordRef, ...]) -> WeightTrend:
        current = records[0] if records else None
        previous = records[1] if len(records) > 1 else None
        if current is None or previous is None:
            return cls(
                records=records,
                current=current,
                previous=previous,
                change_kg=None,
                direction=WeightTrendDirection.UNKNOWN,
            )
        change_grams = current.weight_grams - previous.weight_grams
        direction = WeightTrendDirection.STABLE
        if change_grams < 0:
            direction = WeightTrendDirection.DOWN
        elif change_grams > 0:
            direction = WeightTrendDirection.UP
        return cls(
            records=records,
            current=current,
            previous=previous,
            change_kg=Decimal(change_grams) / Decimal("1000"),
            direction=direction,
        )
