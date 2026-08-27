from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExerciseRecordStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    VOIDED = "voided"


class ExerciseRecordCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    activity_name: str = Field(min_length=1, max_length=128)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    steps: int | None = Field(default=None, ge=0, le=200_000)
    distance_meters: int | None = Field(default=None, ge=0, le=1_000_000)
    reported_energy_kcal: int | None = Field(default=None, ge=0, le=20_000)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @field_validator("activity_name", "note")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Exercise text cannot be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Exercise occurrence time must be timezone-aware")
        return value

    @property
    def occurred_at_utc(self) -> datetime:
        return self.occurred_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ExerciseRecordRef:
    id: str
    user_id: str
    activity_name: str
    duration_minutes: int | None
    steps: int | None
    distance_meters: int | None
    reported_energy_kcal: int | None
    note: str | None
    occurred_at: datetime
    status: ExerciseRecordStatus
    idempotency_key: str
    source_turn_id: str
    source_item_id: str | None
    source_tool_call_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExerciseRecordCreation:
    record: ExerciseRecordRef
    created: bool
