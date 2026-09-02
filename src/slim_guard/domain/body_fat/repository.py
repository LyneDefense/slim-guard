from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import BodyFatRecord
from slim_guard.db.session import Database
from slim_guard.domain.body_fat.contracts import (
    BodyFatMeasurementCommand,
    BodyFatRecordCreation,
    BodyFatRecordRef,
    BodyFatRecordStatus,
    BodyFatTrend,
)
from slim_guard.domain.body_fat.errors import (
    BodyFatRecordCollision,
    BodyFatSourceMismatch,
)
from slim_guard.domain.source import validate_record_source


class BodyFatRepository:
    """Authoritative, idempotent persistence boundary for body-fat measurements."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(self, command: BodyFatMeasurementCommand) -> BodyFatRecordCreation:
        row = BodyFatRecord(
            user_id=command.user_id,
            body_fat_basis_points=command.basis_points,
            original_value=command.canonical_value,
            measured_at=command.measured_at_utc,
            status=BodyFatRecordStatus.ACTIVE.value,
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
                raise BodyFatSourceMismatch(f"Body-fat {mismatch}")
            session.add(row)
            try:
                await session.commit()
                return BodyFatRecordCreation(record=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(BodyFatRecord).where(
                        BodyFatRecord.idempotency_key == command.idempotency_key
                    )
                )
                if existing is None:
                    raise BodyFatRecordCollision(
                        "Body-fat record conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_record(existing, command)
                return BodyFatRecordCreation(record=self._ref(existing), created=False)

    async def recent_trend(self, user_id: str, *, limit: int = 7) -> BodyFatTrend:
        if not 1 <= limit <= 31:
            raise ValueError("Body-fat trend limit must be between 1 and 31")
        async with self.database.session() as session:
            rows = await session.scalars(
                select(BodyFatRecord)
                .where(
                    BodyFatRecord.user_id == user_id,
                    BodyFatRecord.status == BodyFatRecordStatus.ACTIVE.value,
                )
                .order_by(
                    BodyFatRecord.measured_at.desc(),
                    BodyFatRecord.created_at.desc(),
                    BodyFatRecord.id.desc(),
                )
                .limit(limit)
            )
            return BodyFatTrend.from_records(tuple(self._ref(row) for row in rows))

    @classmethod
    def _assert_same_record(
        cls,
        row: BodyFatRecord,
        command: BodyFatMeasurementCommand,
    ) -> None:
        expected = (
            command.user_id,
            command.basis_points,
            command.canonical_value,
            command.measured_at_utc,
            command.source_turn_id,
            command.source_item_id,
            command.source_tool_call_id,
        )
        actual = (
            row.user_id,
            row.body_fat_basis_points,
            row.original_value,
            cls._as_utc(row.measured_at),
            row.source_turn_id,
            row.source_item_id,
            row.source_tool_call_id,
        )
        if actual != expected:
            raise BodyFatRecordCollision(
                f"Body-fat record idempotency collision: {command.idempotency_key}"
            )

    @classmethod
    def _ref(cls, row: BodyFatRecord) -> BodyFatRecordRef:
        return BodyFatRecordRef(
            id=row.id,
            user_id=row.user_id,
            basis_points=row.body_fat_basis_points,
            original_value=row.original_value,
            measured_at=cls._as_utc(row.measured_at),
            status=BodyFatRecordStatus(row.status),
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
