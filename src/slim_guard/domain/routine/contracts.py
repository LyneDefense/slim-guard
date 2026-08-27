from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReminderKind(StrEnum):
    WEIGHT = "weight"
    MEAL = "meal"
    DAILY_REVIEW = "daily_review"


class RoutineSetting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    local_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_enabled_time(self) -> RoutineSetting:
        if self.enabled and self.local_time is None:
            raise ValueError("An enabled routine requires a local time")
        if self.local_time is not None:
            parsed = time.fromisoformat(self.local_time)
            if parsed.second or parsed.microsecond:
                raise ValueError("Routine time must use minute precision")
        return self


class RoutinePreferenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    weight: RoutineSetting | None = None
    meal: RoutineSetting | None = None
    daily_review: RoutineSetting | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown IANA timezone") from None
        return value

    @model_validator(mode="after")
    def require_change(self) -> RoutinePreferenceCommand:
        if all(
            value is None
            for value in (self.timezone, self.weight, self.meal, self.daily_review)
        ):
            raise ValueError("Routine preference command contains no changes")
        return self


@dataclass(frozen=True, slots=True)
class RoutinePreferenceRef:
    user_id: str
    timezone: str
    weight_reminder_time: str | None
    meal_reminder_time: str | None
    daily_review_time: str | None

    def time_for(self, kind: ReminderKind) -> str | None:
        return {
            ReminderKind.WEIGHT: self.weight_reminder_time,
            ReminderKind.MEAL: self.meal_reminder_time,
            ReminderKind.DAILY_REVIEW: self.daily_review_time,
        }[kind]
