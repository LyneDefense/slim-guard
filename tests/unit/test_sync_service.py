from __future__ import annotations

from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.schemas import SyncMessage, SyncPage
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import FixedReplySyncService
from tests.fakes import FakeWeComClient


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
    client = FakeWeComClient(pages)
    repository = MessageRepository(database)
    service = FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        fixed_reply_text="fixed",
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
        assert all(sent.content == "fixed" for sent in client.sent)
        assert [transition.service_state for transition in client.transitions] == [
            WeComServiceState.SMART_ASSISTANT,
            WeComServiceState.SMART_ASSISTANT,
        ]
        assert await repository.get_cursor("default", "wk-test") == "cursor-3"
        assert await repository.count_outbound() == 2
    finally:
        await database.close()


async def test_image_is_saved_but_not_replied_to(tmp_path) -> None:
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
        fixed_reply_text="fixed",
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
        assert client.sent == []
        assert [transition.service_state for transition in client.transitions] == [
            WeComServiceState.SMART_ASSISTANT
        ]
        assert await repository.count_outbound() == 0
        assert await repository.get_cursor("default", "wk-test") == "done"
    finally:
        await database.close()
