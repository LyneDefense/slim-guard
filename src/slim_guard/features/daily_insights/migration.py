from typing import cast

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.features.daily_insights.models import DailyInsightRecord


async def migrate_daily_insights(connection: AsyncConnection) -> None:
    await connection.run_sync(
        lambda sync: cast(Table, DailyInsightRecord.__table__).create(sync, checkfirst=True)
    )
