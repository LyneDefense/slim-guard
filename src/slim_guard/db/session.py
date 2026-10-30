from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from slim_guard.db.migrations import migrate


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self._ensure_sqlite_parent_exists(url)
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            # Foreign-key enforcement is per connection, not per database.
            # Account erasure must also cascade on connections opened after migrate().
            event.listen(self.engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
        # Register the date projection before the first connection enters the pool.
        from slim_guard.features.health_records.persistence.queries import install_sqlite_dates

        install_sqlite_dates(self)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _ensure_sqlite_parent_exists(url: str) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return
        path_text = url.removeprefix(prefix)
        if path_text in {":memory:", ""} or path_text.startswith("file:"):
            return
        Path(path_text).expanduser().parent.mkdir(parents=True, exist_ok=True)

    async def create_schema(self) -> None:
        await self.migrate()

    async def migrate(self) -> tuple[str, ...]:
        async with self.engine.connect() as connection:
            sqlite = connection.dialect.name == "sqlite"
            if sqlite:
                await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                await connection.commit()
            try:
                async with connection.begin():
                    completed = await migrate(connection)
            finally:
                if sqlite:
                    if connection.in_transaction():
                        await connection.rollback()
                    await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    await connection.commit()
            return completed

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()


def _enable_sqlite_foreign_keys(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
