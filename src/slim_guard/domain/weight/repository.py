from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    WeightRecord,
)
from slim_guard.db.session import Database
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
            await self._validate_source(session, command)
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
        if not 1 <= limit <= 31:
            raise ValueError("Weight trend limit must be between 1 and 31")
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

    @staticmethod
    async def _validate_source(
        session: AsyncSession,
        command: WeightMeasurementCommand,
    ) -> None:
        source_user_id = await session.scalar(
            select(AgentThreadRecord.user_id)
            .join(AgentTurnRecord, AgentTurnRecord.thread_id == AgentThreadRecord.id)
            .where(AgentTurnRecord.id == command.source_turn_id)
        )
        if source_user_id is None:
            raise WeightSourceMismatch(
                f"Weight source Turn does not exist: {command.source_turn_id}"
            )
        if source_user_id != command.user_id:
            raise WeightSourceMismatch("Weight source Turn belongs to another user")
        if command.source_item_id is None:
            return
        source_item_turn_id = await session.scalar(
            select(AgentItemRecord.turn_id).where(
                AgentItemRecord.id == command.source_item_id
            )
        )
        if source_item_turn_id != command.source_turn_id:
            raise WeightSourceMismatch("Weight source Item does not belong to its Turn")

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
