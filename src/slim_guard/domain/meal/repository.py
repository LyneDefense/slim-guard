from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import MealRecord
from slim_guard.db.session import Database
from slim_guard.domain.meal.contracts import (
    MealFood,
    MealRecordCommand,
    MealRecordCreation,
    MealRecordRef,
    MealRecordStatus,
    MealType,
)
from slim_guard.domain.meal.errors import MealRecordCollision, MealSourceMismatch
from slim_guard.domain.source import validate_record_source


class MealRepository:
    """Authoritative, idempotent persistence boundary for meal records."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(self, command: MealRecordCommand) -> MealRecordCreation:
        foods_json = self._foods_json(command.foods)
        row = MealRecord(
            user_id=command.user_id,
            meal_type=command.meal_type.value,
            foods_json=foods_json,
            note=command.note,
            occurred_at=command.occurred_at_utc,
            status=MealRecordStatus.ACTIVE.value,
            idempotency_key=command.idempotency_key,
            source_turn_id=command.source_turn_id,
            source_item_id=command.source_item_id,
            source_tool_call_id=command.source_tool_call_id,
        )
        async with self.database.session() as session:
            mismatch = await validate_record_source(
                session,
                user_id=command.user_id,
                source_turn_id=command.source_turn_id,
                source_item_id=command.source_item_id,
            )
            if mismatch is not None:
                raise MealSourceMismatch(f"Meal {mismatch}")
            session.add(row)
            try:
                await session.commit()
                return MealRecordCreation(record=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(MealRecord).where(
                        MealRecord.idempotency_key == command.idempotency_key
                    )
                )
                if existing is None:
                    raise MealRecordCollision(
                        "Meal record conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_record(existing, command, foods_json)
                return MealRecordCreation(record=self._ref(existing), created=False)

    async def recent(self, user_id: str, *, limit: int = 10) -> tuple[MealRecordRef, ...]:
        if not 1 <= limit <= 31:
            raise ValueError("Meal record limit must be between 1 and 31")
        async with self.database.session() as session:
            rows = await session.scalars(
                select(MealRecord)
                .where(
                    MealRecord.user_id == user_id,
                    MealRecord.status == MealRecordStatus.ACTIVE.value,
                )
                .order_by(
                    MealRecord.occurred_at.desc(),
                    MealRecord.created_at.desc(),
                    MealRecord.id.desc(),
                )
                .limit(limit)
            )
            return tuple(self._ref(row) for row in rows)

    @classmethod
    def _assert_same_record(
        cls,
        row: MealRecord,
        command: MealRecordCommand,
        foods_json: str,
    ) -> None:
        expected = (
            command.user_id,
            command.meal_type.value,
            foods_json,
            command.note,
            command.occurred_at_utc,
            command.source_turn_id,
            command.source_item_id,
            command.source_tool_call_id,
        )
        actual = (
            row.user_id,
            row.meal_type,
            row.foods_json,
            row.note,
            cls._as_utc(row.occurred_at),
            row.source_turn_id,
            row.source_item_id,
            row.source_tool_call_id,
        )
        if actual != expected:
            raise MealRecordCollision(
                f"Meal record idempotency collision: {command.idempotency_key}"
            )

    @classmethod
    def _ref(cls, row: MealRecord) -> MealRecordRef:
        foods_payload = json.loads(row.foods_json)
        if not isinstance(foods_payload, list):
            raise ValueError(f"Meal foods are not a list: {row.id}")
        return MealRecordRef(
            id=row.id,
            user_id=row.user_id,
            meal_type=MealType(row.meal_type),
            foods=tuple(MealFood.model_validate(food) for food in foods_payload),
            note=row.note,
            occurred_at=cls._as_utc(row.occurred_at),
            status=MealRecordStatus(row.status),
            idempotency_key=row.idempotency_key,
            source_turn_id=row.source_turn_id,
            source_item_id=row.source_item_id,
            source_tool_call_id=row.source_tool_call_id,
            created_at=cls._as_utc(row.created_at),
        )

    @staticmethod
    def _foods_json(foods: tuple[MealFood, ...]) -> str:
        return json.dumps(
            [food.model_dump(mode="json") for food in foods],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
