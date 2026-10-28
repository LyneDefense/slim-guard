"""Meal photos are durable attachments, separate from mutable field evidence."""

import json
from typing import cast

from sqlalchemy import Table, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import AgentItemRecord, ImageAssetRecord, MealRecord
from slim_guard.features.health_records.persistence.edit_sources import MealEditSource


async def migrate_meal_media(connection: AsyncConnection) -> None:
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("meal_records"))
    if "photo_asset_ids_json" not in {column["name"] for column in columns}:
        await connection.execute(
            text(
                "ALTER TABLE meal_records ADD COLUMN "
                "photo_asset_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        )
    await connection.run_sync(cast(Table, MealEditSource.__table__).create, checkfirst=True)
    rows = (
        (
            await connection.execute(
                select(
                    MealRecord.id,
                    MealRecord.user_id,
                    MealRecord.source_turn_id,
                    MealRecord.source_item_id,
                    MealRecord.source_refs_json,
                    MealRecord.photo_asset_ids_json,
                )
            )
        )
        .mappings()
        .all()
    )
    turn_counts: dict[str, int] = {}
    for row in rows:
        turn_counts[row["source_turn_id"]] = turn_counts.get(row["source_turn_id"], 0) + 1
    for row in rows:
        if json.loads(row["photo_asset_ids_json"]):
            continue
        refs = json.loads(row["source_refs_json"])
        photos = list(
            await connection.scalars(
                select(ImageAssetRecord.id)
                .where(
                    ImageAssetRecord.user_id == row["user_id"],
                    ImageAssetRecord.id.in_(refs),
                )
                .order_by(ImageAssetRecord.created_at, ImageAssetRecord.id)
            )
        )
        # Legacy records without image evidence are only backfilled when a single
        # meal was created from that message; never copy all turn images to many meals.
        if not photos and turn_counts[row["source_turn_id"]] == 1:
            source_payload = await connection.scalar(
                select(AgentItemRecord.payload_json).where(
                    AgentItemRecord.id == row["source_item_id"],
                        AgentItemRecord.turn_id == row["source_turn_id"],
                )
            )
            source = json.loads(source_payload) if source_payload else {}
            source_message = source.get("source_message_id")
            attachments = await connection.scalars(
                select(AgentItemRecord.payload_json)
                .where(
                    AgentItemRecord.turn_id == row["source_turn_id"],
                    AgentItemRecord.item_type == "image_attachment",
                )
                .order_by(AgentItemRecord.sequence)
            )
            candidates = []
            for raw in attachments:
                attachment = json.loads(raw)
                if source_message and attachment.get("source_message_id") == source_message:
                    candidates.append(attachment.get("asset_id"))
            photos = list(
                await connection.scalars(
                    select(ImageAssetRecord.id)
                    .where(
                        ImageAssetRecord.id.in_(candidates),
                        ImageAssetRecord.user_id == row["user_id"],
                    )
                    .order_by(ImageAssetRecord.created_at, ImageAssetRecord.id)
                )
            )
        if photos:
            await connection.execute(
                update(MealRecord)
                .where(MealRecord.id == row["id"])
                .values(
                    photo_asset_ids_json=json.dumps(photos[:8]),
                )
            )
