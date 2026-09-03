from __future__ import annotations

from sqlalchemy import insert, inspect, text

from slim_guard.db.models import SchemaMigrationRecord
from slim_guard.db.session import Database


async def test_existing_database_receives_body_fat_table_additively(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SchemaMigrationRecord.__table__.create(
                    sync_connection,
                    checkfirst=True,
                )
            )
            await connection.execute(
                insert(SchemaMigrationRecord).values(
                    version="20260831_01_interaction_tracing"
                )
            )

        completed = await database.migrate()
        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )

        assert completed == (
            "20260902_01_body_fat_records",
            "20260902_02_memory_evidence_refs",
            "20260902_03_memory_index_outbox",
            "20260903_01_mobile_accounts",
        )
        assert "body_fat_records" in table_names
    finally:
        await database.close()


async def test_existing_memory_rows_backfill_their_original_evidence_item(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-upgrade.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SchemaMigrationRecord.__table__.create(
                    sync_connection,
                    checkfirst=True,
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE user_memory_facts ("
                    "id VARCHAR(36) PRIMARY KEY, source_item_id VARCHAR(36) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO user_memory_facts (id, source_item_id) "
                    "VALUES ('memory-1', 'item-1')"
                )
            )
            for version in (
                "20260831_01_interaction_tracing",
                "20260902_01_body_fat_records",
            ):
                await connection.execute(
                    insert(SchemaMigrationRecord).values(version=version)
                )

        completed = await database.migrate()
        async with database.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(
                        "user_memory_facts"
                    )
                }
            )
            evidence_item_id = await connection.scalar(
                text(
                    "SELECT evidence_item_id FROM user_memory_facts "
                    "WHERE id = 'memory-1'"
                )
            )

        assert completed == (
            "20260902_02_memory_evidence_refs",
            "20260902_03_memory_index_outbox",
            "20260903_01_mobile_accounts",
        )
        assert "evidence_item_id" in columns
        assert evidence_item_id == "item-1"
    finally:
        await database.close()
