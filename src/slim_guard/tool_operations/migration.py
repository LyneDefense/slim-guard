from typing import cast

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.tool_operations.models import ToolOperationRecord


async def migrate_tool_operations(connection: AsyncConnection) -> None:
    await connection.run_sync(cast(Table, ToolOperationRecord.__table__).create, checkfirst=True)
