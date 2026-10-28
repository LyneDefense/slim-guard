"""Single-transaction record writes and immutable execution receipts."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentTurnRecord
from slim_guard.db.session import Database
from slim_guard.domain.source import validate_record_source
from slim_guard.features.health_records.commands import (
    CreateRecord,
    RecordReceipt,
    RecordWriteContext,
    UpdateRecord,
)
from slim_guard.features.health_records.mutations import apply_record_patch
from slim_guard.features.health_records.persistence.evidence import (
    validate_evidence,
    validate_meal_photos,
)
from slim_guard.features.health_records.persistence.projection import (
    RECORD_MODELS,
    row_values,
    to_view,
)
from slim_guard.features.health_records.persistence.receipts import RecordMutationReceipt
from slim_guard.features.health_records.provenance import FieldEvidence
from slim_guard.features.health_records.references import (
    RecordConflict,
    RecordError,
    RecordReference,
)
from slim_guard.features.health_records.views import RecordView

TaskCommit = Callable[
    [AsyncSession, RecordWriteContext, UpdateRecord, Any], Awaitable[tuple[str, ...]]
]


class HealthRecordStore:
    def __init__(self, database: Database, *, task_commit: TaskCommit | None = None) -> None:
        self.database = database
        self._task_commit = task_commit

    async def get(self, user_id: str, kind: str, record_id: str) -> RecordView:
        model = RECORD_MODELS.get(kind)
        if model is None:
            raise RecordError("Unknown record kind")
        async with self.database.session() as session:
            row = await session.scalar(
                select(model).where(model.id == record_id, model.user_id == user_id)
            )
            if row is None:
                raise RecordError("Record not found for this user")
            return to_view(kind, row)

    async def receipt(self, user_id: str, operation_id: str) -> RecordReceipt | None:
        async with self.database.session() as session:
            row = await session.get(RecordMutationReceipt, operation_id)
            if row is None or row.user_id != user_id:
                return None
            return RecordReceipt.model_validate_json(row.result_json)

    async def write(
        self, context: RecordWriteContext, command: CreateRecord | UpdateRecord
    ) -> RecordReceipt:
        # tool_call_id is an attempt identity; operation identity freezes the command.
        payload = {
            "context": context.model_dump(exclude={"tool_call_id"}),
            "command": command.model_dump(mode="json"),
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            async with self.database.session() as session, session.begin():
                previous = await session.get(RecordMutationReceipt, context.operation_id)
                if previous:
                    return self._replay(previous, context, fingerprint)
                mismatch = await validate_record_source(
                    session,
                    user_id=context.user_id,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                )
                if mismatch:
                    raise RecordError(mismatch)
                turn_thread = await session.scalar(
                    select(AgentTurnRecord.thread_id).where(AgentTurnRecord.id == context.turn_id)
                )
                if turn_thread != context.thread_id:
                    raise RecordError("Source Turn does not belong to this thread")
                now = datetime.now(UTC)
                if isinstance(command, CreateRecord):
                    await validate_meal_photos(session, context.user_id, command.content)
                    for evidence in command.field_provenance:
                        await validate_evidence(session, context.user_id, evidence)
                    record = RecordView(
                        ref=RecordReference(
                            kind=command.content.kind, record_id=str(uuid4()), revision=1
                        ),
                        status="active",
                        occurrence=command.occurrence,
                        content=command.content,
                        recorded_at=now,
                        updated_at=now,
                        field_provenance=command.field_provenance,
                        recognition_issues=command.recognition_issues,
                        source_refs=tuple(
                            sorted(
                                {ref for p in command.field_provenance for ref in p.source_refs}
                                | ({context.source_item_id} if context.source_item_id else set())
                            )
                        ),
                    )
                    model = RECORD_MODELS[record.ref.kind]
                    session.add(
                        model(
                            id=record.ref.record_id,
                            user_id=context.user_id,
                            idempotency_key=context.operation_id,
                            source_turn_id=context.turn_id,
                            source_item_id=context.source_item_id,
                            source_tool_call_id=context.tool_call_id,
                            created_at=now,
                            **row_values(record),
                        )
                    )
                    result = RecordReceipt(
                        operation_id=context.operation_id, created=True, changed=True, record=record
                    )
                else:
                    model = RECORD_MODELS[command.target.kind]
                    row = await session.scalar(
                        select(model)
                        .where(
                            model.id == command.target.record_id, model.user_id == context.user_id
                        )
                        .with_for_update()
                    )
                    if row is None:
                        raise RecordError("Record not found for this user")
                    if row.retired_reason == "legacy_superseded" and command.patch.kind == "status":
                        raise RecordError("A historically replaced record cannot be restored")
                    for patch_evidence in _patch_evidence(command.patch.model_dump()):
                        await validate_evidence(session, context.user_id, patch_evidence)
                    changed = apply_record_patch(
                        to_view(command.target.kind, row),
                        command.patch,
                        expected_revision=command.target.revision,
                        now=now,
                    )
                    await validate_meal_photos(session, context.user_id, changed.record.content)
                    if changed.changed:
                        updated = await session.execute(
                            update(model)
                            .where(
                                model.id == row.id,
                                model.user_id == context.user_id,
                                model.revision == command.target.revision,
                            )
                            .values(**row_values(changed.record))
                            .returning(model.id)
                        )
                        if updated.scalar_one_or_none() is None:
                            raise RecordConflict("Record revision changed")
                    tasks: tuple[str, ...] = ()
                    if self._task_commit is not None:
                        tasks = await self._task_commit(session, context, command, changed)
                    elif command.complete_tasks:
                        raise RecordError("Task transaction adapter is required")
                    result = RecordReceipt(
                        operation_id=context.operation_id,
                        created=False,
                        changed=changed.changed,
                        record=changed.record,
                        changed_fields=changed.changed_fields,
                        cleared_issue_ids=changed.cleared_issue_ids,
                        task_updates=tasks,
                    )
                session.add(
                    RecordMutationReceipt(
                        operation_id=context.operation_id,
                        user_id=context.user_id,
                        record_kind=result.record.ref.kind,
                        record_id=result.record.ref.record_id,
                        resulting_revision=result.record.ref.revision,
                        command_hash=fingerprint,
                        result_json=result.model_dump_json(),
                        created_at=now,
                    )
                )
                await session.flush()
                return result
        except IntegrityError as exc:
            async with self.database.session() as session:
                previous = await session.get(RecordMutationReceipt, context.operation_id)
                if previous:
                    return self._replay(previous, context, fingerprint)
            raise RecordConflict("Write conflicted; no duplicate write was applied") from exc

    @staticmethod
    def _replay(
        row: RecordMutationReceipt, context: RecordWriteContext, fingerprint: str
    ) -> RecordReceipt:
        if row.user_id != context.user_id or row.command_hash != fingerprint:
            raise RecordConflict("Operation identity reused with a different command")
        return RecordReceipt.model_validate_json(row.result_json)


def _patch_evidence(value: Any) -> list[FieldEvidence]:
    if isinstance(value, dict):
        if "source_kind" in value:
            return [FieldEvidence.model_validate(value)]
        return [e for child in value.values() for e in _patch_evidence(child)]
    if isinstance(value, (list, tuple)):
        return [e for child in value for e in _patch_evidence(child)]
    return []
