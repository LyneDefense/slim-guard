from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import ExerciseRecord
from slim_guard.db.session import Database
from slim_guard.domain.exercise.contracts import (
    ExerciseRecordCommand,
    ExerciseRecordCreation,
    ExerciseRecordRef,
    ExerciseRecordStatus,
)
from slim_guard.domain.exercise.errors import (
    ExerciseRecordCollision,
    ExerciseSourceMismatch,
)
from slim_guard.domain.source import validate_record_source


class ExerciseRepository:
    """Authoritative, idempotent persistence boundary for exercise records."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(self, command: ExerciseRecordCommand) -> ExerciseRecordCreation:
        row = ExerciseRecord(
            user_id=command.user_id,
            activity_name=command.activity_name,
            duration_minutes=command.duration_minutes,
            steps=command.steps,
            distance_meters=command.distance_meters,
            reported_energy_kcal=command.reported_energy_kcal,
            note=command.note,
            occurred_at=command.occurred_at_utc,
            status=ExerciseRecordStatus.ACTIVE.value,
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
                raise ExerciseSourceMismatch(f"Exercise {mismatch}")
            session.add(row)
            try:
                await session.commit()
                return ExerciseRecordCreation(record=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(ExerciseRecord).where(
                        ExerciseRecord.idempotency_key == command.idempotency_key
                    )
                )
                if existing is None:
                    raise ExerciseRecordCollision(
                        "Exercise record conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_record(existing, command)
                return ExerciseRecordCreation(record=self._ref(existing), created=False)

    async def recent(
        self,
        user_id: str,
        *,
        limit: int = 10,
    ) -> tuple[ExerciseRecordRef, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Exercise record limit must be between 1 and 100")
        async with self.database.session() as session:
            rows = await session.scalars(
                select(ExerciseRecord)
                .where(
                    ExerciseRecord.user_id == user_id,
                    ExerciseRecord.status == ExerciseRecordStatus.ACTIVE.value,
                )
                .order_by(
                    ExerciseRecord.occurred_at.desc(),
                    ExerciseRecord.created_at.desc(),
                    ExerciseRecord.id.desc(),
                )
                .limit(limit)
            )
            return tuple(self._ref(row) for row in rows)

    @classmethod
    def _assert_same_record(
        cls,
        row: ExerciseRecord,
        command: ExerciseRecordCommand,
    ) -> None:
        expected = (
            command.user_id,
            command.activity_name,
            command.duration_minutes,
            command.steps,
            command.distance_meters,
            command.reported_energy_kcal,
            command.note,
            command.occurred_at_utc,
            command.source_turn_id,
            command.source_item_id,
            command.source_tool_call_id,
        )
        actual = (
            row.user_id,
            row.activity_name,
            row.duration_minutes,
            row.steps,
            row.distance_meters,
            row.reported_energy_kcal,
            row.note,
            cls._as_utc(row.occurred_at),
            row.source_turn_id,
            row.source_item_id,
            row.source_tool_call_id,
        )
        if actual != expected:
            raise ExerciseRecordCollision(
                f"Exercise record idempotency collision: {command.idempotency_key}"
            )

    @classmethod
    def _ref(cls, row: ExerciseRecord) -> ExerciseRecordRef:
        return ExerciseRecordRef(
            id=row.id,
            user_id=row.user_id,
            activity_name=row.activity_name,
            duration_minutes=row.duration_minutes,
            steps=row.steps,
            distance_meters=row.distance_meters,
            reported_energy_kcal=row.reported_energy_kcal,
            note=row.note,
            occurred_at=cls._as_utc(row.occurred_at),
            status=ExerciseRecordStatus(row.status),
            idempotency_key=row.idempotency_key,
            source_turn_id=row.source_turn_id,
            source_item_id=row.source_item_id,
            source_tool_call_id=row.source_tool_call_id,
            created_at=cls._as_utc(row.created_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
