from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from slim_guard.db.session import Database
from slim_guard.features.daily_insights.contracts import InsightDraft, InsightSnapshot
from slim_guard.features.daily_insights.models import DailyInsightRecord


@dataclass(frozen=True)
class InsightJob:
    snapshot: InsightSnapshot
    status: str
    token: str | None
    previous: InsightDraft | None
    previous_snapshot: InsightSnapshot | None
    generated_at: datetime | None
    failure_code: str | None


class InsightRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def request(
        self, snapshot: InsightSnapshot, now: datetime, *, retry: bool = False
    ) -> InsightJob:
        # A unique owner/day key and CAS updates work across multiple API workers.
        for _ in range(3):
            async with self.database.session() as session:
                row = await session.get(DailyInsightRecord, (snapshot.user_id, snapshot.day))
                if row is None:
                    row = DailyInsightRecord(
                        user_id=snapshot.user_id,
                        day=snapshot.day,
                        fingerprint=snapshot.fingerprint,
                        snapshot_json=snapshot.model_dump_json(),
                        status="pending",
                        available_at=now,
                        attempts=0,
                    )
                    session.add(row)
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                        continue
                    return self._job(row)
                if row.fingerprint != snapshot.fingerprint:
                    old = row.fingerprint
                    changed = await session.execute(
                        update(DailyInsightRecord)
                        .execution_options(synchronize_session=False)
                        .where(
                            DailyInsightRecord.user_id == snapshot.user_id,
                            DailyInsightRecord.day == snapshot.day,
                            DailyInsightRecord.fingerprint == old,
                        )
                        .values(
                            fingerprint=snapshot.fingerprint,
                            snapshot_json=snapshot.model_dump_json(),
                            status="pending",
                            available_at=now + timedelta(seconds=2),
                            lease_token=None,
                            attempts=0,
                            failure_code=None,
                        )
                    )
                    if not changed.rowcount:  # type: ignore[attr-defined]
                        await session.rollback()
                        continue
                    await session.commit()
                    await session.refresh(row)
                elif row.status == "failed" and (retry or self._aware(row.available_at) <= now):
                    await session.execute(
                        update(DailyInsightRecord)
                        .execution_options(synchronize_session=False)
                        .where(
                            DailyInsightRecord.user_id == snapshot.user_id,
                            DailyInsightRecord.day == snapshot.day,
                            DailyInsightRecord.fingerprint == snapshot.fingerprint,
                            DailyInsightRecord.status == "failed",
                        )
                        .values(status="pending", available_at=now, failure_code=None)
                    )
                    await session.commit()
                    await session.refresh(row)
                return self._job(row)
        raise RuntimeError("Concurrent daily-insight requests did not settle")

    async def claim(self, now: datetime) -> InsightJob | None:
        async with self.database.session() as session, session.begin():
            due = or_(
                DailyInsightRecord.status == "pending",
                and_(
                    DailyInsightRecord.status == "running",
                    DailyInsightRecord.available_at <= now,
                ),
            )
            row = await session.scalar(
                select(DailyInsightRecord)
                .where(
                    due,
                    DailyInsightRecord.available_at <= now,
                )
                .order_by(DailyInsightRecord.available_at)
                .limit(1)
            )
            if row is None:
                return None
            token = str(uuid4())
            result = await session.execute(
                update(DailyInsightRecord)
                .execution_options(synchronize_session=False)
                .where(
                    DailyInsightRecord.user_id == row.user_id,
                    DailyInsightRecord.day == row.day,
                    DailyInsightRecord.fingerprint == row.fingerprint,
                    due,
                    DailyInsightRecord.available_at <= now,
                )
                .values(
                    status="running",
                    lease_token=token,
                    available_at=now + timedelta(minutes=2),
                    attempts=DailyInsightRecord.attempts + 1,
                )
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                return None
            await session.refresh(row)
            return self._job(row)

    async def finish(
        self,
        job: InsightJob,
        now: datetime,
        *,
        output: InsightDraft | None = None,
        review: str | None = None,
        failure_code: str | None = None,
    ) -> bool:
        async with self.database.session() as session, session.begin():
            values: dict[str, object] = dict(
                status="ready" if output else "failed",
                lease_token=None,
                available_at=now + timedelta(minutes=5),
                failure_code=failure_code,
                review_json=review,
            )
            if output:
                values.update(
                    output_json=output.model_dump_json(),
                    generated_at=now,
                    output_snapshot_json=job.snapshot.model_dump_json(),
                )
            result = await session.execute(
                update(DailyInsightRecord)
                .execution_options(synchronize_session=False)
                .where(
                    DailyInsightRecord.user_id == job.snapshot.user_id,
                    DailyInsightRecord.day == job.snapshot.day,
                    DailyInsightRecord.fingerprint == job.snapshot.fingerprint,
                    DailyInsightRecord.status == "running",
                    DailyInsightRecord.lease_token == job.token,
                )
                .values(**values)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    def _job(row: DailyInsightRecord) -> InsightJob:
        return InsightJob(
            InsightSnapshot.model_validate_json(row.snapshot_json),
            row.status,
            row.lease_token,
            InsightDraft.model_validate_json(row.output_json) if row.output_json else None,
            InsightSnapshot.model_validate_json(row.output_snapshot_json)
            if row.output_snapshot_json
            else None,
            InsightRepository._aware(row.generated_at) if row.generated_at else None,
            row.failure_code,
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        from datetime import UTC

        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
