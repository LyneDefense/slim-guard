"""One-way schema retirement; never executes frozen approval commands."""

import json
import logging
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import MetaData, Table, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import AgentItemRecord, AgentTurnRecord


async def retire_approval_waits(connection: AsyncConnection) -> None:
    turns = cast(Table, AgentTurnRecord.__table__)
    items = cast(Table, AgentItemRecord.__table__)
    rows = (
        (
            await connection.execute(
                select(turns).where(
                    turns.c.status.in_(("waiting_user_confirmation", "waiting_human_review"))
                )
            )
        )
        .mappings()
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        sequence = await connection.scalar(
            select(func.max(items.c.sequence)).where(items.c.turn_id == row["id"])
        )
        await connection.execute(
            items.insert().values(
                id=str(uuid4()),
                thread_id=row["thread_id"],
                turn_id=row["id"],
                sequence=(sequence or 0) + 1,
                item_type="error",
                status="failed",
                payload_json=json.dumps({"code": "approval_workflow_retired", "executed": False}),
                created_at=now,
            )
        )
        await connection.execute(
            update(turns)
            .where(turns.c.id == row["id"])
            .values(
                status="failed",
                completed_at=now,
                updated_at=now,
            )
        )
    tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    removed = 0
    if "pending_actions" in tables:
        legacy = await connection.run_sync(
            lambda sync: Table("pending_actions", MetaData(), autoload_with=sync)
        )
        removed = await connection.scalar(select(func.count()).select_from(legacy)) or 0
        await connection.run_sync(legacy.drop)
    logging.getLogger(__name__).info(
        "approval_workflow_retired", extra={"turn_count": len(rows), "removed_actions": removed}
    )
