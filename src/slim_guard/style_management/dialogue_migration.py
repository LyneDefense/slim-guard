"""Add optional sample history without changing existing materials or releases."""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def migrate_dialogue_examples(connection: AsyncConnection) -> None:
    columns = await connection.run_sync(
        lambda sync: {c["name"] for c in inspect(sync).get_columns("expression_examples")}
    )
    if "history" not in columns:
        await connection.execute(
            text("ALTER TABLE expression_examples ADD COLUMN history JSON NOT NULL DEFAULT '[]'")
        )
