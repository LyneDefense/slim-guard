from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from slim_guard.db.models import ExerciseRecord, MealRecord, WeightRecord
from slim_guard.db.session import Database


@dataclass(frozen=True, slots=True)
class DailyCheckinStatus:
    user_id: str
    local_date: date
    timezone: str
    weight_count: int
    meal_count: int
    exercise_count: int

    @property
    def has_weight(self) -> bool:
        return self.weight_count > 0

    @property
    def has_meal(self) -> bool:
        return self.meal_count > 0


class DailyCheckinStatusRepository:
    """Computes deterministic local-day completion from authoritative records."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        *,
        user_id: str,
        local_date: date,
        timezone: str,
    ) -> DailyCheckinStatus:
        zone = ZoneInfo(timezone)
        start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
        end = (datetime.combine(local_date, time.min, tzinfo=zone) + timedelta(days=1)).astimezone(
            UTC
        )
        async with self._database.session() as session:
            weight_count = await session.scalar(
                select(func.count(WeightRecord.id)).where(
                    WeightRecord.user_id == user_id,
                    WeightRecord.status == "active",
                    WeightRecord.measured_at >= start,
                    WeightRecord.measured_at < end,
                )
            )
            meal_count = await session.scalar(
                select(func.count(MealRecord.id)).where(
                    MealRecord.user_id == user_id,
                    MealRecord.status == "active",
                    MealRecord.occurred_at >= start,
                    MealRecord.occurred_at < end,
                )
            )
            exercise_count = await session.scalar(
                select(func.count(ExerciseRecord.id)).where(
                    ExerciseRecord.user_id == user_id,
                    ExerciseRecord.status == "active",
                    ExerciseRecord.occurred_at >= start,
                    ExerciseRecord.occurred_at < end,
                )
            )
        return DailyCheckinStatus(
            user_id=user_id,
            local_date=local_date,
            timezone=timezone,
            weight_count=int(weight_count or 0),
            meal_count=int(meal_count or 0),
            exercise_count=int(exercise_count or 0),
        )
