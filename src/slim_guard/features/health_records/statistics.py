"""Public calendar-period statistics over active, dated health records."""

from datetime import date
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecordPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    from_date: date
    to_date: date
    timezone: str

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.from_date > self.to_date:
            raise ValueError("Date range is reversed")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid timezone") from exc
        return self


class RecordCounts(BaseModel):
    weight: int = Field(default=0, ge=0)
    body_fat: int = Field(default=0, ge=0)
    meal: int = Field(default=0, ge=0)
    exercise: int = Field(default=0, ge=0)


class DailyMeasurement(BaseModel):
    record_id: str
    on: date
    value: float
    unit: Literal["kg", "%"]


class RecordPeriodStatistics(BaseModel):
    period: RecordPeriod
    counts: RecordCounts
    weights: tuple[DailyMeasurement, ...] = ()
    body_fat: tuple[DailyMeasurement, ...] = ()
