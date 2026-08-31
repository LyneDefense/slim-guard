from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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
        async with self.engine.begin() as connection:
            return await migrate(connection)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
