from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from slim_guard.db.models import (
    ConversationTaskOperationRecord,
    ConversationTaskRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.mobile.platform import MobilePlatformService
from slim_guard.tasks.repository import ConversationTaskRepository
from tests.unit.tasks.test_tasks import source
from tests.unit.test_working_memory import manifest


async def test_account_deletion_removes_task_snapshots_but_not_other_users(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'delete-account.sqlite'}")
    await db.create_schema()
    now = datetime.now(UTC)
    async with db.session() as session, session.begin():
        session.add_all(
            [SlimGuardUser(id=u, first_seen_at=now, last_seen_at=now) for u in ("user-1", "user-2")]
        )
    await AgentVersionRepository(db).register(manifest())
    repo = ConversationTaskRepository(db)
    try:
        task = await repo.write(await source(db, "待办敏感内容"))
        other = await repo.write(await source(db, "其他用户的待办", user_id="user-2"))
        await MobilePlatformService(
            database=db,
            secret="test-platform-secret-long-enough-for-this-test",
        ).delete_account("user-1")
        async with db.session() as session:
            assert await session.get(ConversationTaskRecord, task.id) is None
            assert await session.get(ConversationTaskRecord, other.id) is not None
            operations = tuple(await session.scalars(select(ConversationTaskOperationRecord)))
            assert {op.task_id for op in operations} == {other.id}
    finally:
        await db.close()
