"""Mobile period selection and projection; storage belongs to health records."""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from slim_guard.features.health_records.service import RecordService
from slim_guard.features.health_records.statistics import (
    DailyMeasurement,
    RecordCounts,
    RecordPeriod,
)


class PeriodProgressView(BaseModel):
    days: Literal[7, 30]
    from_date: date
    to_date: date
    timezone: str
    counts: RecordCounts
    weights: tuple[DailyMeasurement, ...]
    body_fat: tuple[DailyMeasurement, ...]


async def period_progress(
    records: RecordService, user_id: str, *, days: Literal[7, 30], timezone: str, now: datetime
) -> PeriodProgressView:
    if now.utcoffset() is None:
        raise ValueError("An aware current time is required")
    today = now.astimezone(ZoneInfo(timezone)).date()
    period = RecordPeriod(
        from_date=today - timedelta(days=days - 1), to_date=today, timezone=timezone
    )
    statistics = await records.statistics(user_id, period)

    return PeriodProgressView(
        days=days,
        from_date=period.from_date,
        to_date=period.to_date,
        timezone=timezone,
        counts=statistics.counts,
        weights=statistics.weights,
        body_fat=statistics.body_fat,
    )
