from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.routine.contracts import (
    ReminderKind,
    RoutinePreferenceCommand,
    RoutineSetting,
)
from slim_guard.domain.routine.jobs import (
    RoutineJobPlanner,
    RoutineJobRepository,
    RoutineJobStatus,
)
from slim_guard.domain.routine.repository import RoutinePreferenceRepository

NOW = datetime(2026, 8, 27, 0, 30, tzinfo=UTC)  # 08:30 in Asia/Shanghai


async def prepare(
    tmp_path,
) -> tuple[Database, RoutinePreferenceRepository, RoutineJobRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'routine-jobs.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    preferences = RoutinePreferenceRepository(database)
    await preferences.update(
        RoutinePreferenceCommand(
            user_id="user-1",
            timezone="Asia/Shanghai",
            weight=RoutineSetting(enabled=True, local_time="08:00"),
            daily_review=RoutineSetting(enabled=True, local_time="21:00"),
        )
    )
    return database, preferences, RoutineJobRepository(database)


async def test_planner_creates_only_due_jobs_once_per_local_day(tmp_path) -> None:
    database, preferences, jobs = await prepare(tmp_path)
    planner = RoutineJobPlanner(preferences=preferences, jobs=jobs)
    try:
        first = await planner.plan_due(now=NOW)
        repeated = await planner.plan_due(now=NOW + timedelta(minutes=5))

        assert len(first) == 1
        assert first[0].kind is ReminderKind.WEIGHT
        assert first[0].local_date == date(2026, 8, 27)
        assert first[0].scheduled_for == datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
        assert repeated[0].id == first[0].id
    finally:
        await database.close()


async def test_claim_lease_recovers_after_worker_restart_and_finishes_once(
    tmp_path,
) -> None:
    database, preferences, jobs = await prepare(tmp_path)
    planner = RoutineJobPlanner(preferences=preferences, jobs=jobs)
    try:
        planned = await planner.plan_due(now=NOW)
        first_claim = await jobs.claim_due(
            now=NOW,
            lease_duration=timedelta(minutes=2),
        )
        before_expiry = await jobs.claim_due(
            now=NOW + timedelta(minutes=1),
            lease_duration=timedelta(minutes=2),
        )
        recovered = await jobs.claim_due(
            now=NOW + timedelta(minutes=3),
            lease_duration=timedelta(minutes=2),
        )
        finished = await jobs.finish(
            job_id=planned[0].id,
            status=RoutineJobStatus.COMPLETED,
            result_code="delivered",
            completed_at=NOW + timedelta(minutes=3),
        )
        repeated_finish = await jobs.finish(
            job_id=planned[0].id,
            status=RoutineJobStatus.COMPLETED,
            result_code="delivered",
            completed_at=NOW + timedelta(minutes=4),
        )
        stored = await jobs.get(planned[0].id)

        assert len(first_claim) == 1
        assert first_claim[0].attempt_count == 1
        assert before_expiry == ()
        assert len(recovered) == 1
        assert recovered[0].attempt_count == 2
        assert finished is True
        assert repeated_finish is False
        assert stored is not None
        assert stored.status is RoutineJobStatus.COMPLETED
        assert stored.result_code == "delivered"
    finally:
        await database.close()
