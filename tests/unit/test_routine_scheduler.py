from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from slim_guard.agent.runtime import AgentRuntimeResult, AgentScheduledRequest
from slim_guard.db.models import ChannelIdentity, SlimGuardUser, WeComConversation
from slim_guard.db.session import Database
from slim_guard.domain.routine.contracts import (
    RoutinePreferenceCommand,
    RoutineSetting,
)
from slim_guard.domain.routine.jobs import (
    RoutineJobPlanner,
    RoutineJobRepository,
    RoutineJobStatus,
)
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatus
from slim_guard.harness.termination import HarnessTermination
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.proactive_delivery import (
    ProactiveDeliveryPolicy,
    ProactiveDeliveryRepository,
)
from slim_guard.services.routine_scheduler import RoutineSchedulerService

NOW = datetime(2026, 8, 27, 0, 30, tzinfo=UTC)


class FakeCheckins:
    def __init__(self, *, has_weight: bool = False, has_meal: bool = False) -> None:
        self.has_weight = has_weight
        self.has_meal = has_meal

    async def get(self, *, user_id: str, local_date: date, timezone: str):
        return DailyCheckinStatus(
            user_id=user_id,
            local_date=local_date,
            timezone=timezone,
            weight_count=int(self.has_weight),
            meal_count=int(self.has_meal),
            exercise_count=0,
        )


class FakeRuntime:
    def __init__(self) -> None:
        self.requests: list[AgentScheduledRequest] = []

    async def run_scheduled(self, request: AgentScheduledRequest) -> AgentRuntimeResult:
        self.requests.append(request)
        return AgentRuntimeResult(
            thread_id="thread-1",
            turn_id="turn-1",
            agent_version_id="agent-1",
            termination=HarnessTermination.FINAL_RESPONSE,
            final_text="早上好，今天还没有体重记录，方便时空腹称一下吧。",
            failure_code=None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send_text(self, **kwargs) -> None:
        self.sent.append(kwargs)


class FakeConversationControl:
    async def ensure_agent_control(self, conversation):
        return WeComServiceState.SMART_ASSISTANT


async def prepare(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                ChannelIdentity(
                    channel_id="default",
                    external_userid="external-1",
                    user_id="user-1",
                ),
                WeComConversation(
                    channel_id="default",
                    open_kfid="wk-test",
                    external_userid="external-1",
                    service_state=1,
                    last_customer_message_at=NOW - timedelta(hours=1),
                ),
            )
        )
    preferences = RoutinePreferenceRepository(database)
    await preferences.update(
        RoutinePreferenceCommand(
            user_id="user-1",
            timezone="Asia/Shanghai",
            weight=RoutineSetting(enabled=True, local_time="08:00"),
        )
    )
    jobs = RoutineJobRepository(database)
    deliveries = ProactiveDeliveryRepository(database)
    return database, preferences, jobs, deliveries


async def test_scheduler_runs_agent_and_delivers_each_job_once(tmp_path) -> None:
    database, preferences, jobs, deliveries = await prepare(tmp_path)
    runtime = FakeRuntime()
    client = FakeClient()
    scheduler = RoutineSchedulerService(
        planner=RoutineJobPlanner(preferences=preferences, jobs=jobs),
        jobs=jobs,
        preferences=preferences,
        checkins=FakeCheckins(),
        policy=ProactiveDeliveryPolicy(
            repository=deliveries,
            open_kfid="wk-test",
        ),
        deliveries=deliveries,
        runtime=runtime,
        client=client,
        conversation_control=FakeConversationControl(),
    )
    try:
        first = await scheduler.run_once(now=NOW)
        repeated = await scheduler.run_once(now=NOW + timedelta(minutes=1))
        planned = await jobs.get(
            (await RoutineJobPlanner(preferences=preferences, jobs=jobs).plan_due(now=NOW))[0].id
        )

        assert first == 1
        assert repeated == 0
        assert len(runtime.requests) == 1
        assert len(client.sent) == 1
        assert client.sent[0]["msgid"]
        assert planned is not None
        assert planned.status is RoutineJobStatus.COMPLETED
        assert planned.result_code == "delivered"
    finally:
        await database.close()


async def test_scheduler_skips_model_when_user_already_checked_in(tmp_path) -> None:
    database, preferences, jobs, deliveries = await prepare(tmp_path)
    runtime = FakeRuntime()
    client = FakeClient()
    scheduler = RoutineSchedulerService(
        planner=RoutineJobPlanner(preferences=preferences, jobs=jobs),
        jobs=jobs,
        preferences=preferences,
        checkins=FakeCheckins(has_weight=True),
        policy=ProactiveDeliveryPolicy(
            repository=deliveries,
            open_kfid="wk-test",
        ),
        deliveries=deliveries,
        runtime=runtime,
        client=client,
        conversation_control=FakeConversationControl(),
    )
    try:
        completed = await scheduler.run_once(now=NOW)
        planned = (await RoutineJobPlanner(preferences=preferences, jobs=jobs).plan_due(now=NOW))[0]
        stored = await jobs.get(planned.id)

        assert completed == 1
        assert runtime.requests == []
        assert client.sent == []
        assert stored is not None
        assert stored.status is RoutineJobStatus.SKIPPED
        assert stored.result_code == "weight_already_recorded"
    finally:
        await database.close()


async def test_scheduler_reuses_prepared_delivery_after_worker_restart(tmp_path) -> None:
    database, preferences, jobs, deliveries = await prepare(tmp_path)
    planner = RoutineJobPlanner(preferences=preferences, jobs=jobs)
    planned = (await planner.plan_due(now=NOW))[0]
    claimed = await jobs.claim_due(now=NOW, lease_duration=timedelta(minutes=2))
    assert claimed[0].id == planned.id
    policy = ProactiveDeliveryPolicy(repository=deliveries, open_kfid="wk-test")
    eligibility = await policy.evaluate(user_id="user-1", now=NOW)
    assert eligibility.route is not None
    await deliveries.prepare(
        job_id=planned.id,
        route=eligibility.route,
        content="已经冻结的提醒内容",
        source_turn_id="turn-before-crash",
    )
    runtime = FakeRuntime()
    client = FakeClient()
    scheduler = RoutineSchedulerService(
        planner=planner,
        jobs=jobs,
        preferences=preferences,
        checkins=FakeCheckins(),
        policy=policy,
        deliveries=deliveries,
        runtime=runtime,
        client=client,
        conversation_control=FakeConversationControl(),
    )
    try:
        completed = await scheduler.run_once(now=NOW + timedelta(minutes=3))
        stored = await jobs.get(planned.id)

        assert completed == 1
        assert runtime.requests == []
        assert client.sent[0]["content"] == "已经冻结的提醒内容"
        assert stored is not None
        assert stored.status is RoutineJobStatus.COMPLETED
    finally:
        await database.close()
