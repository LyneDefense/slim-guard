from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from slim_guard.db.models import (
    ChannelIdentity,
    InboundMessage,
    InteractionTraceRecord,
    OutboundMessage,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.memory.lifecycle import MemoryLifecycleRepository
from slim_guard.observability.tracing import InteractionTraceRepository


async def test_completed_outbound_body_is_redacted_with_transcript_retention(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'trace-privacy.sqlite3'}")
    await database.create_schema()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=40)
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=old,
                last_seen_at=old,
            )
        )
        session.add(
            InboundMessage(
                channel_id="default",
                msgid="inbound-1",
                open_kfid="wk-test",
                external_userid="external-1",
                msgtype="text",
                origin=3,
                send_time=old,
            )
        )
        session.add(
            OutboundMessage(
                idempotency_key="outbound-1",
                platform_msgid="platform-1",
                channel_id="default",
                inbound_msgid="inbound-1",
                open_kfid="wk-test",
                external_userid="external-1",
                content="包含健康信息的回复正文",
                status="accepted",
                created_at=old,
                completed_at=old,
            )
        )

    try:
        lifecycle = MemoryLifecycleRepository(database)
        result = await lifecycle.scrub_transcript_bodies(
            before=now - timedelta(days=30),
            redacted_at=now,
        )
        async with database.session() as session:
            outbound = await session.get(OutboundMessage, "outbound-1")
        assert result.outbound_message_count == 1
        assert outbound is not None
        assert outbound.content.startswith("[redacted:sha256=")
        second = await lifecycle.scrub_transcript_bodies(
            before=now - timedelta(days=30),
            redacted_at=now,
        )
        assert second.outbound_message_count == 0
    finally:
        await database.close()


async def test_historical_outbound_is_backfilled_without_inventing_agent_link(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'trace-backfill.sqlite3'}")
    await database.create_schema()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        session.add(
            ChannelIdentity(
                channel_id="default",
                external_userid="external-1",
                user_id="user-1",
            )
        )
        session.add(
            InboundMessage(
                channel_id="default",
                msgid="inbound-1",
                open_kfid="wk-test",
                external_userid="external-1",
                msgtype="text",
                origin=3,
                send_time=now,
            )
        )
        session.add(
            OutboundMessage(
                idempotency_key="outbound-1",
                platform_msgid="platform-1",
                channel_id="default",
                inbound_msgid="inbound-1",
                open_kfid="wk-test",
                external_userid="external-1",
                content="历史回复",
                status="accepted",
                created_at=now,
                completed_at=now,
            )
        )

    try:
        traces = InteractionTraceRepository(database)
        assert await traces.backfill_existing() == 1
        assert await traces.backfill_existing() == 0
        async with database.session() as session:
            trace = await session.scalar(select(InteractionTraceRecord))
        assert trace is not None
        assert trace.user_id == "user-1"
        assert trace.generation_status == "unknown"
        assert trace.failure_code == "historical_trace_unlinked"
        assert trace.delivery_status == "accepted"
    finally:
        await database.close()
