from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BodyFatRecordStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    VOIDED = "voided"


class BodyFatTrendDirection(StrEnum):
    DOWN = "down"
    STABLE = "stable"
    UP = "up"
    UNKNOWN = "unknown"


class BodyFatMeasurementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    value: Decimal = Field(gt=0)
    measured_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        if self.measured_at.utcoffset() is None:
            raise ValueError("Body-fat measurement time must be timezone-aware")
        if not 100 <= self.basis_points <= 7500:
            raise ValueError("Body-fat percentage must be between 1% and 75%")
        return self

    @property
    def basis_points(self) -> int:
        return int(
            (self.value * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    @property
    def canonical_value(self) -> str:
        return format(self.value.normalize(), "f")

    @property
    def measured_at_utc(self) -> datetime:
        return self.measured_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class BodyFatRecordRef:
    id: str
    user_id: str
    basis_points: int
    original_value: str
    measured_at: datetime
    status: BodyFatRecordStatus
    idempotency_key: str
    source_turn_id: str
    source_item_id: str | None
    source_tool_call_id: str
    created_at: datetime

    @property
    def percent(self) -> Decimal:
        return Decimal(self.basis_points) / Decimal("100")


@dataclass(frozen=True, slots=True)
class BodyFatRecordCreation:
    record: BodyFatRecordRef
    created: bool


@dataclass(frozen=True, slots=True)
class BodyFatTrend:
    records: tuple[BodyFatRecordRef, ...]
    current: BodyFatRecordRef | None
    previous: BodyFatRecordRef | None
    change_percentage_points: Decimal | None
    direction: BodyFatTrendDirection

    @classmethod
    def from_records(cls, records: tuple[BodyFatRecordRef, ...]) -> BodyFatTrend:
        current = records[0] if records else None
        previous = records[1] if len(records) > 1 else None
        if current is None or previous is None:
            return cls(
                records=records,
                current=current,
                previous=previous,
                change_percentage_points=None,
                direction=BodyFatTrendDirection.UNKNOWN,
            )
        change = current.basis_points - previous.basis_points
        direction = BodyFatTrendDirection.STABLE
        if change < 0:
            direction = BodyFatTrendDirection.DOWN
        elif change > 0:
            direction = BodyFatTrendDirection.UP
        return cls(
            records=records,
            current=current,
            previous=previous,
            change_percentage_points=Decimal(change) / Decimal("100"),
            direction=direction,
        )
