from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import WeightRecord
from slim_guard.db.session import Database
from slim_guard.domain.source import validate_record_source
from slim_guard.domain.weight.contracts import (
    WeightMeasurementCommand,
    WeightMeasurementCondition,
    WeightRecordCreation,
    WeightRecordRef,
    WeightRecordStatus,
    WeightTrend,
    WeightUnit,
)
from slim_guard.domain.weight.errors import WeightRecordCollision, WeightSourceMismatch


class WeightRepository:
    """Authoritative, idempotent persistence boundary for weight measurements."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(self, command: WeightMeasurementCommand) -> WeightRecordCreation:
        row = WeightRecord(
            user_id=command.user_id,
            weight_grams=command.weight_grams,
            original_value=command.canonical_original_value,
            original_unit=command.unit.value,
            measured_at=command.measured_at_utc,
            measurement_condition=command.condition.value,
            status=WeightRecordStatus.ACTIVE.value,
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
                raise WeightSourceMismatch(f"Weight {mismatch}")
            session.add(row)
            try:
                await session.commit()
                return WeightRecordCreation(record=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(WeightRecord).where(
                        WeightRecord.idempotency_key == command.idempotency_key
                    )
                )
                if existing is None:
                    raise WeightRecordCollision(
                        "Weight record conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_record(existing, command)
                return WeightRecordCreation(record=self._ref(existing), created=False)

    async def get(self, record_id: str) -> WeightRecordRef | None:
        async with self.database.session() as session:
            row = await session.get(WeightRecord, record_id)
            return self._ref(row) if row is not None else None

    async def recent_trend(self, user_id: str, *, limit: int = 7) -> WeightTrend:
        if not 1 <= limit <= 100:
            raise ValueError("Weight trend limit must be between 1 and 100")
        async with self.database.session() as session:
            rows = await session.scalars(
                select(WeightRecord)
                .where(
                    WeightRecord.user_id == user_id,
                    WeightRecord.status == WeightRecordStatus.ACTIVE.value,
                )
                .order_by(
                    WeightRecord.measured_at.desc(),
                    WeightRecord.created_at.desc(),
                    WeightRecord.id.desc(),
                )
                .limit(limit)
            )
            return WeightTrend.from_records(tuple(self._ref(row) for row in rows))

    @classmethod
    def _assert_same_record(
        cls,
        row: WeightRecord,
        command: WeightMeasurementCommand,
    ) -> None:
        expected = (
            command.user_id,
            command.weight_grams,
            command.canonical_original_value,
            command.unit.value,
            command.measured_at_utc,
            command.condition.value,
            command.source_turn_id,
            command.source_item_id,
            command.source_tool_call_id,
        )
        actual = (
            row.user_id,
            row.weight_grams,
            row.original_value,
            row.original_unit,
            cls._as_utc(row.measured_at),
            row.measurement_condition,
            row.source_turn_id,
            row.source_item_id,
            row.source_tool_call_id,
        )
        if actual != expected:
            raise WeightRecordCollision(
                f"Weight record idempotency collision: {command.idempotency_key}"
            )

    @classmethod
    def _ref(cls, row: WeightRecord) -> WeightRecordRef:
        return WeightRecordRef(
            id=row.id,
            user_id=row.user_id,
            weight_grams=row.weight_grams,
            original_value=row.original_value,
            original_unit=WeightUnit(row.original_unit),
            measured_at=cls._as_utc(row.measured_at),
            condition=WeightMeasurementCondition(row.measurement_condition),
            status=WeightRecordStatus(row.status),
            idempotency_key=row.idempotency_key,
            source_turn_id=row.source_turn_id,
            source_item_id=row.source_item_id,
            source_tool_call_id=row.source_tool_call_id,
            supersedes_id=row.supersedes_id,
            created_at=cls._as_utc(row.created_at),
            superseded_at=(
                cls._as_utc(row.superseded_at)
                if row.superseded_at is not None
                else None
            ),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
