from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.db.models import InboundMessage
from slim_guard.db.repositories import MessageRepository
from slim_guard.db.session import Database


async def test_backfill_creates_one_user_per_external_id(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'users.sqlite3'}")
    await database.create_schema()
    first_seen = datetime.now(UTC) - timedelta(days=2)
    last_seen = datetime.now(UTC) - timedelta(days=1)
    async with database.session() as session, session.begin():
        session.add_all(
            [
                InboundMessage(
                    channel_id="default",
                    msgid="message-1",
                    open_kfid="wk-test",
                    external_userid="external-1",
                    msgtype="text",
                    origin=3,
                    send_time=first_seen,
                ),
                InboundMessage(
                    channel_id="default",
                    msgid="message-2",
                    open_kfid="wk-test",
                    external_userid="external-1",
                    msgtype="text",
                    origin=3,
                    send_time=last_seen,
                ),
                InboundMessage(
                    channel_id="default",
                    msgid="message-3",
                    open_kfid="wk-test",
                    external_userid="external-2",
                    msgtype="image",
                    origin=3,
                    send_time=last_seen,
                ),
            ]
        )

    repository = MessageRepository(database)
    try:
        assert await repository.backfill_users_from_messages() == 2
        assert await repository.backfill_users_from_messages() == 0
        users = await repository.list_users()
        assert len(users) == 2
        first_user = next(user for user in users if user.external_userid == "external-1")
        assert first_user.first_seen_at == first_seen
        assert first_user.last_seen_at == last_seen
        assert first_user.profile_status == "pending"
    finally:
        await database.close()
