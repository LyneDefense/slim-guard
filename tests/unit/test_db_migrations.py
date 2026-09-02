from __future__ import annotations

from sqlalchemy import insert, inspect

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

        assert completed == ("20260902_01_body_fat_records",)
        assert "body_fat_records" in table_names
    finally:
        await database.close()
