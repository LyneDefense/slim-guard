from datetime import date
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.features.health_records.references import RecordKind
from slim_guard.features.health_records.views import RecordView


class RecordQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: RecordKind
    from_date: date | None = None
    to_date: date | None = None
    source_from: date | None = None
    source_to: date | None = None
    source_ref: str | None = None
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] | None = None
    text: str | None = Field(default=None, max_length=200)
    status: Literal["active", "voided"] = "active"
    timezone: str = "Asia/Shanghai"
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None

    @model_validator(mode="after")
    def bounds(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid timezone") from exc
        for start, end in ((self.from_date, self.to_date), (self.source_from, self.source_to)):
            if start and end and start > end:
                raise ValueError("Date range is reversed")
        if self.meal_type and self.kind != "meal":
            raise ValueError("Meal type applies only to meals")
        return self


class RecordPage(BaseModel):
    records: tuple[RecordView, ...]
    next_cursor: str | None
    has_more: bool
