"""Replace the singleton handoff store, retaining its source identities."""

import json
from typing import cast

from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import ConversationTaskOperationRecord, ConversationTaskRecord


async def migrate_conversation_tasks(connection: AsyncConnection) -> None:
    for model in (ConversationTaskRecord, ConversationTaskOperationRecord):
        table = cast(Table, model.__table__)
        await connection.run_sync(table.create, checkfirst=True)
    tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    if "memory_handoffs" not in tables:
        return
    legacy = await connection.run_sync(
        lambda sync: Table("memory_handoffs", MetaData(), autoload_with=sync)
    )
    target = cast(Table, ConversationTaskRecord.__table__)
    async for row in (await connection.stream(select(legacy))).mappings():
        if await connection.scalar(select(target.c.id).where(target.c.id == row["id"])):
            continue
        source = await connection.scalar(
            text("SELECT payload_json FROM agent_items WHERE id = :id"),
            {"id": row["source_item_id"]},
        )
        payload = json.loads(source) if source else {}
        original_text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(original_text, str):
            original_text = ""
        images = await connection.scalars(
            text(
                "SELECT payload_json FROM agent_items "
                "WHERE turn_id = :turn AND item_type = 'image_attachment'"
            ),
            {"turn": row["source_turn_id"]},
        )
        attachments = [
            p["asset_id"]
            for raw in images
            if isinstance(p := json.loads(raw), dict) and isinstance(p.get("asset_id"), str)
        ]
        await connection.execute(
            target.insert().values(
                id=row["id"],
                user_id=row["user_id"],
                thread_id=row["thread_id"],
                revision=1,
                status="open" if row["status"] == "active" else row["status"],
                objective=row["objective"],
                unresolved_json=row["unresolved_json"] if row["status"] == "active" else "[]",
                evidence_json=json.dumps(
                    [
                        {
                            "source_item_id": row["source_item_id"],
                            "excerpt": original_text[:512],
                            "interpretation": "Migrated unfinished-task summary: "
                            + row["objective"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                source_item_ids_json=json.dumps([row["source_item_id"]]),
                attachment_ids_json=json.dumps(attachments),
                source_turn_id=row["source_turn_id"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                resolved_at=row["resolved_at"],
            )
        )
    await connection.run_sync(legacy.drop)
