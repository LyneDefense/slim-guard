from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, insert, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import Base, SchemaMigrationRecord


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: str
    apply: Callable[[AsyncConnection], Awaitable[None]]


async def _create_application_tables(connection: AsyncConnection) -> None:
    """Additive migration safe for both an existing SQLite DB and a fresh DB."""

    await connection.run_sync(Base.metadata.create_all)


async def _add_memory_evidence_item(connection: AsyncConnection) -> None:
    """Persist the user-authored fact source separately from the current action."""

    await _create_application_tables(connection)
    columns = await connection.run_sync(
        lambda sync_connection: {
            column["name"]
            for column in inspect(sync_connection).get_columns("user_memory_facts")
        }
    )
    if "evidence_item_id" in columns:
        return
    await connection.execute(
        text("ALTER TABLE user_memory_facts ADD COLUMN evidence_item_id VARCHAR(36)")
    )
    await connection.execute(
        text(
            "UPDATE user_memory_facts SET evidence_item_id = source_item_id "
            "WHERE evidence_item_id IS NULL"
        )
    )


MIGRATIONS = (
    SchemaMigration("20260831_01_interaction_tracing", _create_application_tables),
    SchemaMigration("20260902_01_body_fat_records", _create_application_tables),
    SchemaMigration("20260902_02_memory_evidence_refs", _add_memory_evidence_item),
    SchemaMigration("20260902_03_memory_index_outbox", _create_application_tables),
    SchemaMigration("20260903_01_mobile_accounts", _create_application_tables),
    SchemaMigration("20260903_02_mobile_devices_and_bindings", _create_application_tables),
)


async def migrate(connection: AsyncConnection) -> tuple[str, ...]:
    """Apply pending, application-owned schema migrations in version order."""

    # Bootstrap only the migration ledger before querying it. The first migration
    # then creates every application table that is missing from an existing DB.
    await connection.run_sync(
        lambda sync_connection: cast(Table, SchemaMigrationRecord.__table__).create(
            sync_connection,
            checkfirst=True,
        )
    )
    applied = set(await connection.scalars(select(SchemaMigrationRecord.version)))
    completed: list[str] = []
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        await migration.apply(connection)
        await connection.execute(
            insert(SchemaMigrationRecord).values(version=migration.version)
        )
        completed.append(migration.version)
    return tuple(completed)
