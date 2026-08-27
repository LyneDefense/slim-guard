from __future__ import annotations

from sqlalchemy import select

from slim_guard.db.models import UserRoutinePreference
from slim_guard.db.session import Database
from slim_guard.domain.routine.contracts import (
    RoutinePreferenceCommand,
    RoutinePreferenceRef,
    RoutineSetting,
)


class RoutinePreferenceRepository:
    """Persists one user-owned check-in and review schedule."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, user_id: str) -> RoutinePreferenceRef | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(UserRoutinePreference).where(
                    UserRoutinePreference.user_id == user_id
                )
            )
            return self._ref(row) if row is not None else None

    async def update(self, command: RoutinePreferenceCommand) -> RoutinePreferenceRef:
        async with self._database.session() as session, session.begin():
            row = await session.get(UserRoutinePreference, command.user_id)
            if row is None:
                row = UserRoutinePreference(user_id=command.user_id)
                session.add(row)
            if command.timezone is not None:
                row.timezone = command.timezone
            self._apply(row, "weight_reminder_time", command.weight)
            self._apply(row, "meal_reminder_time", command.meal)
            self._apply(row, "daily_review_time", command.daily_review)
            await session.flush()
            return self._ref(row)

    async def list_enabled(self) -> tuple[RoutinePreferenceRef, ...]:
        async with self._database.session() as session:
            rows = await session.scalars(
                select(UserRoutinePreference).where(
                    (UserRoutinePreference.weight_reminder_time.is_not(None))
                    | (UserRoutinePreference.meal_reminder_time.is_not(None))
                    | (UserRoutinePreference.daily_review_time.is_not(None))
                )
            )
            return tuple(self._ref(row) for row in rows)

    @staticmethod
    def _apply(
        row: UserRoutinePreference,
        attribute: str,
        setting: RoutineSetting | None,
    ) -> None:
        if setting is not None:
            setattr(row, attribute, setting.local_time if setting.enabled else None)

    @staticmethod
    def _ref(row: UserRoutinePreference) -> RoutinePreferenceRef:
        return RoutinePreferenceRef(
            user_id=row.user_id,
            timezone=row.timezone,
            weight_reminder_time=row.weight_reminder_time,
            meal_reminder_time=row.meal_reminder_time,
            daily_review_time=row.daily_review_time,
        )
