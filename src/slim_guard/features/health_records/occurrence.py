"""Time precision is data, not a guessed timestamp or a meal classifier."""

from datetime import date, datetime
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator


class Occurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precision: Literal["datetime", "date", "unknown"] = "unknown"
    at: datetime | None = None
    on: date | None = None
    timezone: str = "UTC"
    basis: Literal["user_reported", "inferred", "unknown", "legacy"] = "unknown"

    @model_validator(mode="after")
    def validate_precision(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("A valid IANA timezone is required") from exc
        if self.precision == "datetime":
            if self.at is None or self.at.utcoffset() is None or self.on is not None:
                raise ValueError("datetime requires an aware timestamp and no date-only value")
        elif self.precision == "date":
            if self.on is None or self.at is not None:
                raise ValueError("date requires a local date and no fabricated timestamp")
        elif self.at is not None or self.on is not None:
            raise ValueError("unknown occurrence cannot include a date or timestamp")
        if self.precision == "unknown" and self.basis != "unknown":
            raise ValueError("Unknown occurrence has unknown basis")
        if self.precision != "unknown" and self.basis == "unknown":
            raise ValueError("Known occurrence requires its basis")
        return self

    def local_date(self, timezone: str) -> date | None:
        if self.at is not None:
            return self.at.astimezone(ZoneInfo(timezone)).date()
        return self.on
