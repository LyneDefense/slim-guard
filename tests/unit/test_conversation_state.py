from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.schemas import SyncMessage, SyncPage
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.fixed_reply import FixedReplySyncService
from tests.fakes import FakeReplyAgent, FakeWeComClient


def _build_service(
    *,
    client: FakeWeComClient,
    repository: MessageRepository,
    state_machine: ConversationStateMachine,
    reply_delivery_mode: str = "automatic",
) -> FixedReplySyncService:
    return FixedReplySyncService(
        client=client,
        repository=repository,
        channel_id="default",
        configured_open_kfid="wk-test",
        reply_agent=FakeReplyAgent("agent reply"),
        fallback_reply_text="fallback",
        state_machine=state_machine,
        reply_delivery_mode=reply_delivery_mode,
    )


async def test_timed_out_human_session_is_ended_and_notified(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'human-timeout.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    client = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                msg_list=[
                    SyncMessage(
                        msgid="waiting-for-human",
                        external_userid="user-1",
                        send_time=int((now - timedelta(minutes=11)).timestamp()),
                        origin=3,
                        msgtype="text",
                        text={"content": "anyone there?"},
                    )
                ],
            )
        },
        service_states={"user-1": WeComServiceState.HUMAN},
    )
    repository = MessageRepository(database)
    state_machine = ConversationStateMachine(
        client=client,
        repository=repository,
        human_idle_timeout_seconds=600,
        watchdog_interval_seconds=30,
        human_timeout_message="请再发送一次",
    )
    service = _build_service(client=client, repository=repository, state_machine=state_machine)

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        ended_count = await state_machine.handle_human_timeouts_once(now=now)

        assert client.sent == []
        assert ended_count == 1
        assert client.service_states["user-1"] is WeComServiceState.ENDED
        assert [item.service_state for item in client.transitions] == [WeComServiceState.ENDED]
        assert [item.content for item in client.sent_events] == ["请再发送一次"]
    finally:
        await database.close()


async def test_internal_review_keeps_wecom_in_agent_state_until_approved(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}")
    await database.create_schema()
    client = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                msg_list=[
                    SyncMessage(
                        msgid="needs-review",
                        external_userid="user-1",
                        send_time=int(datetime.now(UTC).timestamp()),
                        origin=3,
                        msgtype="text",
                        text={"content": "review me"},
                    )
                ],
            )
        }
    )
    repository = MessageRepository(database)
    state_machine = ConversationStateMachine(
        client=client,
        repository=repository,
        human_idle_timeout_seconds=600,
        watchdog_interval_seconds=30,
        human_timeout_message="timeout",
    )
    service = _build_service(
        client=client,
        repository=repository,
        state_machine=state_machine,
        reply_delivery_mode="internal_review",
    )

    try:
        await service.sync_and_reply(callback_token="token", open_kfid="wk-test")
        assert client.sent == []
        assert client.service_states["user-1"] is WeComServiceState.SMART_ASSISTANT

        pending = await repository.list_pending_reviews()
        assert len(pending) == 1
        approved = await repository.approve_review(pending[0].idempotency_key)
        assert approved is not None
        await service.dispatch_approved(approved)
        assert [item.content for item in client.sent] == ["agent reply"]
    finally:
        await database.close()
