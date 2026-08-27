from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import RoutineJobRecord
from slim_guard.db.session import Database
from slim_guard.domain.routine.contracts import ReminderKind, RoutinePreferenceRef
from slim_guard.domain.routine.repository import RoutinePreferenceRepository


class RoutineJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RoutineJobRef:
    id: str
    user_id: str
    kind: ReminderKind
    local_date: date
    scheduled_for: datetime
    status: RoutineJobStatus
    attempt_count: int
    lease_expires_at: datetime | None
    result_turn_id: str | None
    result_code: str | None


class RoutineJobRepository:
    """Durable daily job ledger with unique planning and expiring execution leases."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure_planned(
        self,
        *,
        preference: RoutinePreferenceRef,
        kind: ReminderKind,
        local_date: date,
    ) -> RoutineJobRef:
        local_time = preference.time_for(kind)
        if local_time is None:
            raise ValueError("Cannot plan a disabled routine")
        scheduled_for = datetime.combine(
            local_date,
            time.fromisoformat(local_time),
            tzinfo=ZoneInfo(preference.timezone),
        ).astimezone(UTC)
        row = RoutineJobRecord(
            user_id=preference.user_id,
            job_kind=kind.value,
            local_date=local_date.isoformat(),
            scheduled_for=scheduled_for,
        )
        async with self._database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return self._ref(row)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(RoutineJobRecord).where(
                        RoutineJobRecord.user_id == preference.user_id,
                        RoutineJobRecord.job_kind == kind.value,
                        RoutineJobRecord.local_date == local_date.isoformat(),
                    )
                )
                if existing is None:
                    raise
                return self._ref(existing)

    async def claim_due(
        self,
        *,
        now: datetime,
        lease_duration: timedelta,
        limit: int = 20,
    ) -> tuple[RoutineJobRef, ...]:
        self._validate_aware(now)
        if lease_duration <= timedelta(0):
            raise ValueError("Routine job lease duration must be positive")
        if not 1 <= limit <= 100:
            raise ValueError("Routine job claim limit must be between 1 and 100")
        async with self._database.session() as session, session.begin():
            candidates = await session.scalars(
                select(RoutineJobRecord)
                .where(
                    RoutineJobRecord.scheduled_for <= now,
                    or_(
                        RoutineJobRecord.status == RoutineJobStatus.PENDING.value,
                        (
                            (RoutineJobRecord.status == RoutineJobStatus.RUNNING.value)
                            & (RoutineJobRecord.lease_expires_at <= now)
                        ),
                    ),
                )
                .order_by(RoutineJobRecord.scheduled_for, RoutineJobRecord.id)
                .limit(limit)
            )
            claimed: list[RoutineJobRef] = []
            for candidate in candidates:
                previous_status = candidate.status
                statement = (
                    update(RoutineJobRecord)
                    .where(
                        RoutineJobRecord.id == candidate.id,
                        RoutineJobRecord.status == previous_status,
                    )
                    .values(
                        status=RoutineJobStatus.RUNNING.value,
                        attempt_count=RoutineJobRecord.attempt_count + 1,
                        lease_expires_at=now + lease_duration,
                    )
                )
                if previous_status == RoutineJobStatus.RUNNING.value:
                    statement = statement.where(RoutineJobRecord.lease_expires_at <= now)
                result = await session.execute(
                    statement.execution_options(synchronize_session=False)
                )
                if self._rowcount(result) != 1:
                    continue
                await session.refresh(candidate)
                claimed.append(self._ref(candidate))
            return tuple(claimed)

    async def finish(
        self,
        *,
        job_id: str,
        status: RoutineJobStatus,
        result_code: str,
        completed_at: datetime,
        result_turn_id: str | None = None,
    ) -> bool:
        self._validate_aware(completed_at)
        if status not in {
            RoutineJobStatus.COMPLETED,
            RoutineJobStatus.SKIPPED,
            RoutineJobStatus.FAILED,
        }:
            raise ValueError("Routine job can only finish in a terminal status")
        async with self._database.session() as session, session.begin():
            result = await session.execute(
                update(RoutineJobRecord)
                .where(
                    RoutineJobRecord.id == job_id,
                    RoutineJobRecord.status == RoutineJobStatus.RUNNING.value,
                )
                .values(
                    status=status.value,
                    result_code=result_code,
                    result_turn_id=result_turn_id,
                    lease_expires_at=None,
                    completed_at=completed_at,
                )
            )
            return self._rowcount(result) == 1

    async def get(self, job_id: str) -> RoutineJobRef | None:
        async with self._database.session() as session:
            row = await session.get(RoutineJobRecord, job_id)
            return self._ref(row) if row is not None else None

    @classmethod
    def _ref(cls, row: RoutineJobRecord) -> RoutineJobRef:
        return RoutineJobRef(
            id=row.id,
            user_id=row.user_id,
            kind=ReminderKind(row.job_kind),
            local_date=date.fromisoformat(row.local_date),
            scheduled_for=cls._as_utc(row.scheduled_for),
            status=RoutineJobStatus(row.status),
            attempt_count=row.attempt_count,
            lease_expires_at=(
                cls._as_utc(row.lease_expires_at)
                if row.lease_expires_at is not None
                else None
            ),
            result_turn_id=row.result_turn_id,
            result_code=row.result_code,
        )

    @staticmethod
    def _rowcount(result: object) -> int:
        return int(result.rowcount) if isinstance(result, CursorResult) else 0

    @staticmethod
    def _validate_aware(value: datetime) -> None:
        if value.utcoffset() is None:
            raise ValueError("Routine job time must be timezone-aware")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class RoutineJobPlanner:
    """Creates today's idempotent jobs from user-local routine preferences."""

    def __init__(
        self,
        *,
        preferences: RoutinePreferenceRepository,
        jobs: RoutineJobRepository,
    ) -> None:
        self._preferences = preferences
        self._jobs = jobs

    async def plan_due(self, *, now: datetime) -> tuple[RoutineJobRef, ...]:
        if now.utcoffset() is None:
            raise ValueError("Routine planner time must be timezone-aware")
        planned: list[RoutineJobRef] = []
        for preference in await self._preferences.list_enabled():
            local_now = now.astimezone(ZoneInfo(preference.timezone))
            for kind in ReminderKind:
                configured_time = preference.time_for(kind)
                if configured_time is None:
                    continue
                scheduled_local = datetime.combine(
                    local_now.date(),
                    time.fromisoformat(configured_time),
                    tzinfo=local_now.tzinfo,
                )
                if scheduled_local > local_now:
                    continue
                planned.append(
                    await self._jobs.ensure_planned(
                        preference=preference,
                        kind=kind,
                        local_date=local_now.date(),
                    )
                )
        return tuple(planned)
