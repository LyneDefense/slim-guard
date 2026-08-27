from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.client import WeComMedia
from slim_guard.integrations.wecom_kf.errors import WeComAPIError
from slim_guard.integrations.wecom_kf.schemas import (
    CustomerProfile,
    CustomerProfileBatch,
    SyncMessage,
    SyncPage,
)
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import FixedReplySyncService
from slim_guard.services.reply_agent import ReplyRequest
from tests.fakes import FakeReplyAgent, FakeWeComClient


class ProfileFailingWeComClient(FakeWeComClient):
    async def get_customer_profiles(self, *, external_userids: list[str]) -> CustomerProfileBatch:
        raise WeComAPIError(60011, "no permission")


class FailingReplyAgent(FakeReplyAgent):
    async def generate_reply(self, request: ReplyRequest) -> str:
        self.requests.append(request)
        raise RuntimeError("model unavailable")


async def test_sync_follows_empty_page_and_deduplicates(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'sync.sqlite3'}")
    await database.create_schema()
    pages = {
        None: SyncPage(
            next_cursor="cursor-1",
            has_more=True,
            msg_list=[
                SyncMessage(
                    msgid="message-1",
                    external_userid="user-1",
                    send_time=1_700_000_000,
                    origin=3,
                    msgtype="text",
                    text={"content": "hello"},
                )
            ],
        ),
        "cursor-1": SyncPage(next_cursor="cursor-2", has_more=True, msg_list=[]),
        "cursor-2": SyncPage(
            next_cursor="cursor-3",
            has_more=False,
            msg_list=[
                SyncMessage(
                    msgid="message-2",
                    external_userid="user-2",
                    send_time=1_700_000_001,
                    origin=3,
                    msgtype="text",
                    text={"content": "hi"},
                ),
                SyncMessage(
                    msgid="servicer-message",
                    external_userid="user-2",
                    send_time=1_700_000_002,
                    origin=4,
                    msgtype="text",
                ),
            ],
        ),
        "cursor-3": SyncPage(next_cursor="cursor-3", has_more=False, msg_list=[]),
    }
    client = FakeWeComClient(
        pages,
        customer_profiles={
            "user-1": CustomerProfile(
                external_userid="user-1",
                nickname="客户甲",
                avatar="https://example.com/a.png",
                gender=1,
                unionid="union-1",
            ),
            "user-2": CustomerProfile(
                external_userid="user-2",
                nickname="客户乙",
                gender=2,
            ),
        },
    )
    repository = MessageRepository(database)
    reply_agent = FakeReplyAgent("agent reply")
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=reply_agent,
        fallback_reply_text="fallback",
        state_machine=ConversationStateMachine(
            client=client,
            repository=repository,
            human_idle_timeout_seconds=600,
            watchdog_interval_seconds=30,
            human_timeout_message="timeout",
        ),
        reply_delivery_mode="automatic",
    )

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")

        assert client.sync_cursors == [None, "cursor-1", "cursor-2", "cursor-3"]
        assert [sent.external_userid for sent in client.sent] == ["user-1", "user-2"]
        assert all(sent.content == "agent reply" for sent in client.sent)
        assert [request.text for request in reply_agent.requests] == ["hello", "hi"]
        assert [request.source_message_id for request in reply_agent.requests] == [
            "message-1",
            "message-2",
        ]
        assert [request.channel_id for request in reply_agent.requests] == [
            "default",
            "default",
        ]
        assert [request.occurred_at for request in reply_agent.requests] == [
            datetime.fromtimestamp(1_700_000_000, tz=UTC),
            datetime.fromtimestamp(1_700_000_001, tz=UTC),
        ]
        assert [transition.service_state for transition in client.transitions] == [
            WeComServiceState.SMART_ASSISTANT,
            WeComServiceState.SMART_ASSISTANT,
        ]
        assert await repository.get_cursor("default", "wk-test") == "cursor-3"
        assert await repository.count_outbound() == 2
        users = await repository.list_users()
        assert len(users) == 2
        assert len({user.id for user in users}) == 2
        users_by_external_id = {user.external_userid: user for user in users}
        assert users_by_external_id["user-1"].nickname == "客户甲"
        assert users_by_external_id["user-1"].unionid == "union-1"
        assert users_by_external_id["user-2"].nickname == "客户乙"
        assert all(user.profile_status == "synced" for user in users)
        assert client.customer_profile_requests == [["user-1"], ["user-2"]]
    finally:
        await database.close()


async def test_image_is_downloaded_and_sent_to_agent(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'image.sqlite3'}")
    await database.create_schema()
    client = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="image-message",
                        external_userid="user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="image",
                        image={"media_id": "media-1"},
                    )
                ],
            )
        },
        media={
            "media-1": WeComMedia(
                content=b"\x89PNG\r\n\x1a\nimage",
                content_type="image/png",
            )
        },
    )
    repository = MessageRepository(database)
    reply_agent = FakeReplyAgent("看到体重秤了")
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=reply_agent,
        fallback_reply_text="fallback",
        state_machine=ConversationStateMachine(
            client=client,
            repository=repository,
            human_idle_timeout_seconds=600,
            watchdog_interval_seconds=30,
            human_timeout_message="timeout",
        ),
        reply_delivery_mode="automatic",
    )

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        assert [sent.content for sent in client.sent] == ["看到体重秤了"]
        assert client.media_requests == ["media-1"]
        assert len(reply_agent.requests) == 1
        assert reply_agent.requests[0].image_bytes == b"\x89PNG\r\n\x1a\nimage"
        assert [transition.service_state for transition in client.transitions] == [
            WeComServiceState.SMART_ASSISTANT
        ]
        assert await repository.count_outbound() == 1
        assert await repository.get_cursor("default", "wk-test") == "done"
    finally:
        await database.close()


async def test_profile_failure_does_not_block_reply(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'profile-failure.sqlite3'}")
    await database.create_schema()
    client = ProfileFailingWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                msg_list=[
                    SyncMessage(
                        msgid="message-1",
                        external_userid="user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="text",
                        text={"content": "hello"},
                    )
                ],
            )
        }
    )
    repository = MessageRepository(database)
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=FakeReplyAgent("agent reply"),
        fallback_reply_text="fallback",
        state_machine=ConversationStateMachine(
            client=client,
            repository=repository,
            human_idle_timeout_seconds=600,
            watchdog_interval_seconds=30,
            human_timeout_message="timeout",
        ),
        reply_delivery_mode="automatic",
    )

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        users = await repository.list_users()
        assert [sent.content for sent in client.sent] == ["agent reply"]
        assert len(users) == 1
        assert users[0].profile_status == "error"
    finally:
        await database.close()


async def test_agent_failure_sends_configured_fallback(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent-failure.sqlite3'}")
    await database.create_schema()
    client = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                msg_list=[
                    SyncMessage(
                        msgid="message-1",
                        external_userid="user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="text",
                        text={"content": "hello"},
                    )
                ],
            )
        }
    )
    repository = MessageRepository(database)
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=FailingReplyAgent(),
        fallback_reply_text="请稍后再试",
        state_machine=ConversationStateMachine(
            client=client,
            repository=repository,
            human_idle_timeout_seconds=600,
            watchdog_interval_seconds=30,
            human_timeout_message="timeout",
        ),
        reply_delivery_mode="automatic",
    )

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        assert [sent.content for sent in client.sent] == ["请稍后再试"]
    finally:
        await database.close()


async def test_outbox_recovery_sends_frozen_plan_without_regenerating(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'outbox-recovery.sqlite3'}")
    await database.create_schema()
    client = FakeWeComClient({})
    repository = MessageRepository(database)
    stored = await repository.store_page(
        channel_id="default",
        open_kfid="wk-test",
        messages=[
            SyncMessage(
                msgid="message-before-crash",
                external_userid="user-1",
                send_time=1_700_000_000,
                origin=3,
                msgtype="text",
                text={"content": "hello"},
            )
        ],
        next_cursor="done",
        fallback_reply_text="fallback",
        allowed_message_types=frozenset({"text"}),
        reply_delivery_mode="automatic",
        profile_refresh_seconds=86_400,
    )
    await repository.update_outbound_content(
        stored.plans[0].idempotency_key,
        "进程退出前已经冻结的回复",
    )
    reply_agent = FakeReplyAgent("不应重新生成")
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=reply_agent,
        fallback_reply_text="fallback",
        state_machine=ConversationStateMachine(
            client=client,
            repository=repository,
            human_idle_timeout_seconds=600,
            watchdog_interval_seconds=30,
            human_timeout_message="timeout",
        ),
        reply_delivery_mode="automatic",
        outbox_send_stale_seconds=120,
    )
    try:
        recovered = await service.handle_outbox_recovery_once(
            now=datetime.now(UTC) + timedelta(minutes=3)
        )

        assert recovered == 1
        assert [sent.content for sent in client.sent] == ["进程退出前已经冻结的回复"]
        assert reply_agent.requests == []
        assert await repository.list_recoverable_outbound(
            stale_before=datetime.now(UTC) + timedelta(hours=1)
        ) == []
    finally:
        await database.close()
