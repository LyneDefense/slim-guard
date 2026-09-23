"""Validate task provenance; do not interpret answers or choose business steps."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentItemRecord, AgentThreadRecord
from slim_guard.domain.source import validate_record_source
from slim_guard.tasks.contracts import TaskError, TaskRef, TaskWrite


async def read_task_sources(
    session: AsyncSession,
    task: TaskRef,
    *,
    offset: int,
) -> tuple[dict[str, Any], ...]:
    sources = []
    for item_id in task.source_item_ids[offset : offset + 20]:
        row = await session.scalar(
            select(AgentItemRecord)
            .join(
                AgentThreadRecord,
                AgentThreadRecord.id == AgentItemRecord.thread_id,
            )
            .where(
                AgentItemRecord.id == item_id,
                AgentThreadRecord.user_id == task.user_id,
                AgentItemRecord.thread_id == task.thread_id,
                AgentItemRecord.item_type.in_(("user_message", "image_attachment")),
            )
        )
        if row is None:
            continue
        payload = json.loads(row.payload_json)
        original = str(payload.get("text") or "")
        sources.append(
            {
                "source_item_id": row.id,
                "kind": row.item_type,
                "text": original[:2000],
                "text_truncated": len(original) > 2000,
                "asset_id": payload.get("asset_id"),
                "redacted": bool(payload.get("_redacted")),
            }
        )
    return tuple(sources)


async def validate_sources(
    session: AsyncSession,
    command: TaskWrite,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    mismatch = await validate_record_source(
        session,
        user_id=command.user_id,
        source_turn_id=command.source_turn_id,
        source_item_id=command.source_item_id,
    )
    if mismatch:
        raise TaskError("Task update source is not owned by the current user/turn")
    ids = tuple(dict.fromkeys((*command.source_item_ids, command.source_item_id)))
    attachment_ids: list[str] = []
    current_has_attachment = False
    for item_id in ids:
        item = await session.scalar(
            select(AgentItemRecord)
            .join(
                AgentThreadRecord,
                AgentThreadRecord.id == AgentItemRecord.thread_id,
            )
            .where(
                AgentItemRecord.id == item_id,
                AgentThreadRecord.user_id == command.user_id,
                AgentItemRecord.thread_id == command.thread_id,
                AgentItemRecord.status == "completed",
            )
        )
        if item is None or item.item_type not in {"user_message", "image_attachment"}:
            raise TaskError("Task sources must be user messages or attachments in this thread")
        payload = json.loads(item.payload_json)
        if item_id == command.source_item_id:
            text = payload.get("text")
            if not isinstance(text, str):
                text = ""
            if command.evidence_excerpt:
                if item.item_type != "user_message" or command.evidence_excerpt not in text:
                    raise TaskError("Evidence excerpt must match the current user message")
            elif item.item_type == "user_message" and text.strip():
                raise TaskError("A text update requires a nonempty current-message excerpt")
        # Attachments belong to their preceding user message, not every message in a thread.
        siblings = tuple(
            await session.scalars(
                select(AgentItemRecord)
                .where(
                    AgentItemRecord.turn_id == item.turn_id,
                    AgentItemRecord.sequence >= item.sequence,
                    AgentItemRecord.status == "completed",
                    AgentItemRecord.item_type.in_(("user_message", "image_attachment")),
                )
                .order_by(AgentItemRecord.sequence)
            )
        )
        for sibling in siblings:
            if sibling.id != item.id and sibling.item_type == "user_message":
                break
            if sibling.item_type == "image_attachment":
                asset_id = json.loads(sibling.payload_json).get("asset_id")
                if isinstance(asset_id, str):
                    attachment_ids.append(asset_id)
                    if item_id == command.source_item_id:
                        current_has_attachment = True
    if not command.evidence_excerpt and not current_has_attachment:
        raise TaskError("An image-only update requires a persisted attachment source")
    return ids, tuple(dict.fromkeys(attachment_ids))
