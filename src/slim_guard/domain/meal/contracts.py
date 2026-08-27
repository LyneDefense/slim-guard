from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    UNSPECIFIED = "unspecified"


class MealRecordStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    VOIDED = "voided"


class MealFood(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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


class MealRecordCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    meal_type: MealType = MealType.UNSPECIFIED
    foods: tuple[MealFood, ...] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Meal note cannot be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Meal occurrence time must be timezone-aware")
        return value

    @property
    def occurred_at_utc(self) -> datetime:
        return self.occurred_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MealRecordRef:
    id: str
    user_id: str
    meal_type: MealType
    foods: tuple[MealFood, ...]
    note: str | None
    occurred_at: datetime
    status: MealRecordStatus
    idempotency_key: str
    source_turn_id: str
    source_item_id: str | None
    source_tool_call_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MealRecordCreation:
    record: MealRecordRef
    created: bool
