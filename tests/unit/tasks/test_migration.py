from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, inspect, select, text

from slim_guard.db.models import ConversationTaskRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.tasks.migration import migrate_conversation_tasks
from tests.unit.tasks.test_tasks import source
from tests.unit.test_working_memory import manifest


async def test_legacy_handoffs_migrate_and_old_table_is_removed(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'migration.sqlite'}")
    await db.create_schema()
    now = datetime.now(UTC)
    async with db.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    await AgentVersionRepository(db).register(manifest())
    cmd = await source(db, "下次继续制定计划")
    legacy = Table(
        "memory_handoffs",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("user_id", String),
        Column("thread_id", String),
        Column("status", String),
        Column("objective", String),
        Column("unresolved_json", Text),
        Column("source_turn_id", String),
        Column("source_item_id", String),
        Column("created_at", DateTime),
        Column("expires_at", DateTime),
        Column("resolved_at", DateTime, nullable=True),
    )
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(legacy.create)
            await connection.execute(
                legacy.insert().values(
                    id="legacy-task",
                    user_id="user-1",
                    thread_id=cmd.thread_id,
                    status="active",
                    objective="制定计划",
                    unresolved_json='["选择日期"]',
                    source_turn_id=cmd.source_turn_id,
                    source_item_id=cmd.source_item_id,
                    created_at=now,
                    expires_at=now + timedelta(days=14),
                )
            )
            await migrate_conversation_tasks(connection)
            await migrate_conversation_tasks(connection)
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            columns = await connection.run_sync(lambda c: inspect(c).get_columns("pending_actions"))
            assert "memory_handoffs" not in tables
            assert "task_id" in {c["name"] for c in columns}
        async with db.session() as session:
            rows = tuple(await session.scalars(select(ConversationTaskRecord)))
            assert len(rows) == 1
            assert rows[0].id == "legacy-task"
            assert rows[0].status == "open"
            assert "下次继续制定计划" in rows[0].evidence_json
            assert cmd.source_item_id in rows[0].source_item_ids_json
    finally:
        await db.close()


async def test_existing_authorization_table_gets_task_link_without_changing_grants(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'authorization-upgrade.sqlite'}")
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE pending_actions "
                    "(id TEXT PRIMARY KEY, tool_name TEXT, status TEXT)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO pending_actions VALUES ('kept', 'delete_record', 'pending'), "
                    "('retired', 'confirm_dishes', 'pending')"
                )
            )
            await migrate_conversation_tasks(connection)
            assert (
                await connection.execute(text("SELECT id, status, task_id FROM pending_actions"))
            ).all() == [("kept", "pending", None)]
    finally:
        await db.close()
