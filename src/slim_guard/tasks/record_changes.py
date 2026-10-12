"""Task-owned database participant in the shared record transaction."""

import json
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import ConversationTaskRecord
from slim_guard.features.health_records.commands import RecordWriteContext, UpdateRecord
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.features.health_records.views import RecordChangeResult
from slim_guard.tasks.record_requests import RecordRequest


async def commit_record_tasks(
    session: AsyncSession,
    context: RecordWriteContext,
    command: UpdateRecord,
    result: RecordChangeResult,
) -> tuple[str, ...]:
    rows = (
        await session.scalars(
            select(ConversationTaskRecord)
            .where(
                ConversationTaskRecord.user_id == context.user_id,
                ConversationTaskRecord.status == "open",
            )
            .with_for_update()
        )
    ).all()
    explicit = {task.task_id: task.expected_revision for task in command.complete_tasks}
    updated: list[str] = []
    for row in rows:
        request = RecordRequest.model_validate_json(row.request_json) if row.request_json else None
        related = request is not None and any(
            target.record_id == command.target.record_id and target.kind == command.target.kind
            for target in request.selected_targets
        )
        if row.id in explicit:
            if row.thread_id != context.thread_id or not related:
                raise RecordError("Task is not associated with this record request")
            expiry = (
                row.expires_at.replace(tzinfo=UTC)
                if row.expires_at.tzinfo is None
                else row.expires_at
            )
            if expiry <= datetime.now(UTC) or row.revision != explicit[row.id]:
                raise RecordConflict("Task expired or revision changed")
            status = "resolved"
        elif related and result.record.status == "voided":
            status = "cancelled"
        elif related and request and request.known_changes and request.known_changes.kind == "meal":
            referenced = {
                getattr(change, "item_id", None) for change in request.known_changes.food_changes
            }
            if not (referenced & set(result.removed_item_ids)):
                continue
            status = "cancelled"
        else:
            continue
        if request is not None:
            request = request.model_copy(
                update={
                    "executed_operation_refs": (
                        *request.executed_operation_refs,
                        context.operation_id,
                    )
                }
            )
        effect = await session.execute(
            update(ConversationTaskRecord)
            .where(
                ConversationTaskRecord.id == row.id,
                ConversationTaskRecord.revision == row.revision,
                ConversationTaskRecord.status == "open",
            )
            .values(
                status=status,
                revision=row.revision + 1,
                unresolved_json=json.dumps([]),
                request_json=request.model_dump_json() if request else None,
                resolved_at=datetime.now(UTC),
            )
            .returning(ConversationTaskRecord.id)
        )
        if effect.scalar_one_or_none() is None:
            raise RecordConflict("Task changed during record transaction")
        updated.append(row.id)
    if set(explicit) - set(updated):
        raise RecordConflict("A task is closed, missing, or belongs to another user")
    return tuple(updated)
