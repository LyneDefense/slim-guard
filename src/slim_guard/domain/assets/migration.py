"""Preserve existing chat image bytes while removing the former Agent TTL."""

import json

from sqlalchemy import Table, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    ImageAssetRecord,
)


async def retain_chat_attachments(connection: AsyncConnection) -> None:
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("image_assets"))
    expiry = next(column for column in columns if column["name"] == "expires_at")
    names = {column["name"] for column in columns}
    for name in ("source_text", "observation_json"):
        if name not in names:
            await connection.execute(text(f"ALTER TABLE image_assets ADD COLUMN {name} TEXT"))
    if not expiry["nullable"]:
        if connection.dialect.name == "postgresql":
            await connection.execute(
                text("ALTER TABLE image_assets ALTER COLUMN expires_at DROP NOT NULL")
            )
        elif connection.dialect.name == "sqlite":
            # No table references image_assets by FK. Rebuild only this table;
            # retain IDs, source associations, bytes and the uniqueness constraint.
            await connection.execute(text("ALTER TABLE image_assets RENAME TO image_assets_ttl"))
            for index in await connection.run_sync(
                lambda sync: inspect(sync).get_indexes("image_assets_ttl")
            ):
                index_name = index["name"]
                if index_name and not index_name.startswith("sqlite_autoindex"):
                    quoted = connection.dialect.identifier_preparer.quote(index_name)
                    await connection.execute(text(f"DROP INDEX {quoted}"))
            table: Table = ImageAssetRecord.__table__  # type: ignore[assignment]
            await connection.run_sync(table.create)
            column_names = ", ".join(column.name for column in table.columns)
            await connection.execute(
                text(
                    f"INSERT INTO image_assets ({column_names}) "
                    f"SELECT {column_names} FROM image_assets_ttl"
                )
            )
            await connection.execute(text("DROP TABLE image_assets_ttl"))
        else:
            raise RuntimeError("Unsupported attachment migration database")
    # The previous expiry was imposed by AgentRuntime, not a user deletion request.
    # Already purged images cannot be recovered by this migration.
    await connection.execute(text("UPDATE image_assets SET expires_at = NULL"))
    await _backfill_observations(connection)


async def _backfill_observations(connection: AsyncConnection) -> None:
    """Keep usable old image observations, without importing them as user facts."""
    tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    if not {"agent_items", "agent_turns", "agent_threads"} <= tables:
        return
    rows = await connection.stream(
        select(AgentItemRecord.payload_json, AgentItemRecord.turn_id, AgentThreadRecord.user_id)
        .join(AgentTurnRecord, AgentTurnRecord.id == AgentItemRecord.turn_id)
        .join(AgentThreadRecord, AgentThreadRecord.id == AgentTurnRecord.thread_id)
        .where(AgentItemRecord.item_type == "tool_result", AgentItemRecord.status == "completed")
        .order_by(AgentItemRecord.created_at, AgentItemRecord.sequence)
    )
    async for payload_json, turn_id, user_id in rows:
        try:
            execution = json.loads(payload_json)["execution"]
            if execution.get("tool_name") != "inspect_image":
                continue
            result = execution["result"]
            if result.get("status") != "succeeded":
                continue
            output = result["output"]
            asset_id, description = output["asset_id"], output["description"]
            if not isinstance(asset_id, str) or not isinstance(description, str):
                continue
            annotation = json.dumps(
                {
                    "author": "vision_model",
                    "description": description,
                    "turn_id": turn_id,
                    "tool_call_id": execution.get("tool_call_id"),
                },
                ensure_ascii=False,
            )
        except (KeyError, TypeError, AttributeError, ValueError):
            continue  # Invalid legacy records cannot become trusted attachment context.
        await connection.execute(
            update(ImageAssetRecord)
            .where(ImageAssetRecord.id == asset_id, ImageAssetRecord.user_id == user_id)
            .values(observation_json=annotation)
        )
