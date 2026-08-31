from __future__ import annotations

import asyncio

from slim_guard.config import Settings
from slim_guard.db.session import Database


async def _main() -> None:
    database = Database(Settings().database_url)
    try:
        applied = await database.migrate()
    finally:
        await database.close()
    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Database schema is up to date.")


if __name__ == "__main__":
    asyncio.run(_main())
