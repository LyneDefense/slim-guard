from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentItemRecord, AgentThreadRecord, AgentTurnRecord


async def validate_record_source(
    session: AsyncSession,
    *,
    user_id: str,
    source_turn_id: str,
    source_item_id: str | None,
) -> str | None:
    """Return a safe mismatch reason, or None when the Harness source is trusted."""

    source_user_id = await session.scalar(
        select(AgentThreadRecord.user_id)
        .join(AgentTurnRecord, AgentTurnRecord.thread_id == AgentThreadRecord.id)
        .where(AgentTurnRecord.id == source_turn_id)
    )
    if source_user_id is None:
        return f"Source Turn does not exist: {source_turn_id}"
    if source_user_id != user_id:
        return "Source Turn belongs to another user"
    if source_item_id is None:
        return None
    source_item_turn_id = await session.scalar(
        select(AgentItemRecord.turn_id).where(AgentItemRecord.id == source_item_id)
    )
    if source_item_turn_id != source_turn_id:
        return "Source Item does not belong to its Turn"
    return None
