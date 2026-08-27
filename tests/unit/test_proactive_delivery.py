from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from slim_guard.db.models import (
    ChannelIdentity,
    RoutineJobRecord,
    SlimGuardUser,
    WeComConversation,
)
from slim_guard.db.session import Database
from slim_guard.services.proactive_delivery import (
    ProactiveDeliveryPolicy,
    ProactiveDeliveryRepository,
    ProactiveDeliveryStatus,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


async def prepare(tmp_path) -> tuple[Database, ProactiveDeliveryRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'proactive.sqlite3'}")
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
        for index in range(1, 5):
            session.add(
                RoutineJobRecord(
                    id=f"job-{index}",
                    user_id="user-1",
                    job_kind="weight",
                    local_date=date(2026, 8, 20 + index).isoformat(),
                    scheduled_for=NOW,
                    status="running",
                )
            )
    return database, ProactiveDeliveryRepository(database)


async def test_policy_requires_recent_route_and_reserves_platform_quota(tmp_path) -> None:
    database, repository = await prepare(tmp_path)
    policy = ProactiveDeliveryPolicy(
        repository=repository,
        open_kfid="wk-test",
        max_messages_per_window=1,
    )
    try:
        eligible = await policy.evaluate(user_id="user-1", now=NOW)
        assert eligible.allowed is True
        assert eligible.route is not None

        delivery = await repository.prepare(
            job_id="job-1",
            route=eligible.route,
            content="记得称体重哦。",
            source_turn_id="turn-1",
        )
        assert await repository.claim(
            job_id=delivery.job_id,
            now=NOW,
            retry_after=timedelta(minutes=2),
        )
        limited = await policy.evaluate(user_id="user-1", now=NOW)

        assert limited.allowed is False
        assert limited.code == "proactive_quota_reserved"
    finally:
        await database.close()


async def test_delivery_is_idempotent_and_stale_send_can_retry(tmp_path) -> None:
    database, repository = await prepare(tmp_path)
    policy = ProactiveDeliveryPolicy(repository=repository, open_kfid="wk-test")
    try:
        eligibility = await policy.evaluate(user_id="user-1", now=NOW)
        assert eligibility.route is not None
        first = await repository.prepare(
            job_id="job-1",
            route=eligibility.route,
            content="记得称体重哦。",
            source_turn_id="turn-1",
        )
        repeated = await repository.prepare(
            job_id="job-1",
            route=eligibility.route,
            content="记得称体重哦。",
            source_turn_id="turn-1",
        )
        first_claim = await repository.claim(
            job_id="job-1",
            now=NOW,
            retry_after=timedelta(minutes=2),
        )
        early_claim = await repository.claim(
            job_id="job-1",
            now=NOW + timedelta(minutes=1),
            retry_after=timedelta(minutes=2),
        )
        retry_claim = await repository.claim(
            job_id="job-1",
            now=NOW + timedelta(minutes=3),
            retry_after=timedelta(minutes=2),
        )
        await repository.complete(
            job_id="job-1",
            status=ProactiveDeliveryStatus.ACCEPTED,
            now=NOW + timedelta(minutes=3),
        )

        assert repeated.platform_msgid == first.platform_msgid
        assert first_claim is True
        assert early_claim is False
        assert retry_claim is True
    finally:
        await database.close()


async def test_delivery_rejects_changed_content_for_same_job(tmp_path) -> None:
    database, repository = await prepare(tmp_path)
    policy = ProactiveDeliveryPolicy(repository=repository, open_kfid="wk-test")
    try:
        eligibility = await policy.evaluate(user_id="user-1", now=NOW)
        assert eligibility.route is not None
        await repository.prepare(
            job_id="job-1",
            route=eligibility.route,
            content="第一版",
            source_turn_id="turn-1",
        )
        with pytest.raises(ValueError, match="idempotency collision"):
            await repository.prepare(
                job_id="job-1",
                route=eligibility.route,
                content="被修改的第二版",
                source_turn_id="turn-2",
            )
    finally:
        await database.close()
