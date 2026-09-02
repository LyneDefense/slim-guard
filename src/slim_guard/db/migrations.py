from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import Base, SchemaMigrationRecord


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: str
    apply: Callable[[AsyncConnection], Awaitable[None]]


async def _create_application_tables(connection: AsyncConnection) -> None:
    """Additive migration safe for both an existing SQLite DB and a fresh DB."""

    await connection.run_sync(Base.metadata.create_all)


MIGRATIONS = (
    SchemaMigration("20260831_01_interaction_tracing", _create_application_tables),
    SchemaMigration("20260902_01_body_fat_records", _create_application_tables),
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
