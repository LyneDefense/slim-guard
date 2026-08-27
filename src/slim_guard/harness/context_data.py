from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.weight.repository import WeightRepository


class ContextDataProvider(Protocol):
    """Loads bounded, trusted facts for one user before a model call."""

    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
    ) -> Mapping[str, Any]: ...


class EmptyContextDataProvider:
    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
    ) -> Mapping[str, Any]:
        return {}


class AuthoritativeContextDataProvider:
    """Builds compact cross-Turn context from authoritative domain records."""

    def __init__(
        self,
        *,
        database: Database,
        weights: WeightRepository,
        meals: MealRepository,
        exercise: ExerciseRepository,
        routines: RoutinePreferenceRepository | None = None,
        weight_limit: int = 7,
        meal_limit: int = 10,
        exercise_limit: int = 10,
    ) -> None:
        self._database = database
        self._weights = weights
        self._meals = meals
        self._exercise = exercise
        self._routines = routines
        self._weight_limit = weight_limit
        self._meal_limit = meal_limit
        self._exercise_limit = exercise_limit

    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
    ) -> Mapping[str, Any]:
        if current_time.utcoffset() is None:
            raise ValueError("Context data time must be timezone-aware")
        profile, weight_trend, meals, exercise = await asyncio.gather(
            self._profile(user_id),
            self._weights.recent_trend(user_id, limit=self._weight_limit),
            self._meals.recent(user_id, limit=self._meal_limit),
            self._exercise.recent(user_id, limit=self._exercise_limit),
        )
        context: dict[str, Any] = {
            "recent_weights": [
                {
                    "weight_kg": self._decimal_text(record.weight_kg),
                    "measured_at": record.measured_at.isoformat(),
                    "condition": record.condition.value,
                }
                for record in weight_trend.records
            ],
            "recent_meals": [
                {
                    "meal_type": record.meal_type.value,
                    "foods": [
                        {
                            "name": food.name,
                            **({"portion": food.portion} if food.portion else {}),
                        }
                        for food in record.foods
                    ],
                    "occurred_at": record.occurred_at.isoformat(),
                    **({"note": record.note} if record.note else {}),
                }
                for record in meals
            ],
            "recent_exercise": [
                {
                    "activity_name": record.activity_name,
                    "occurred_at": record.occurred_at.isoformat(),
                    **(
                        {"duration_minutes": record.duration_minutes}
                        if record.duration_minutes is not None
                        else {}
                    ),
                    **({"steps": record.steps} if record.steps is not None else {}),
                    **(
                        {"distance_meters": record.distance_meters}
                        if record.distance_meters is not None
                        else {}
                    ),
                    **(
                        {"reported_energy_kcal": record.reported_energy_kcal}
                        if record.reported_energy_kcal is not None
                        else {}
                    ),
                    **({"note": record.note} if record.note else {}),
                }
                for record in exercise
            ],
        }
        if profile is not None:
            context["profile"] = profile
        if self._routines is not None:
            routine = await self._routines.get(user_id)
            if routine is not None:
                context["checkin_schedule"] = {
                    "timezone": routine.timezone,
                    "weight_reminder_time": routine.weight_reminder_time,
                    "meal_reminder_time": routine.meal_reminder_time,
                    "daily_review_time": routine.daily_review_time,
                }
        return context

    async def _profile(self, user_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(SlimGuardUser).where(SlimGuardUser.id == user_id)
            )
            if row is None:
                return None
            return {
                **({"nickname": row.nickname} if row.nickname else {}),
                "first_seen_at": self._as_aware(row.first_seen_at).isoformat(),
            }

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return format(value.normalize(), "f")

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        return value if value.utcoffset() is not None else value.replace(tzinfo=UTC)
