from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import (
    ConversationTaskOperationRecord,
    ConversationTaskRecord,
    utc_now,
)
from slim_guard.tasks.contracts import (
    TaskConflict,
    TaskError,
    TaskEvidence,
    TaskRef,
    TaskStatus,
    TaskWrite,
)
from slim_guard.tasks.sources import read_task_sources, validate_sources

if TYPE_CHECKING:
    from slim_guard.db.session import Database


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ConversationTaskRepository:
    def __init__(
        self,
        database: Database,
        *,
        ttl: timedelta = timedelta(days=14),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Task TTL must be positive")
        self._database, self._ttl, self._clock = database, ttl, clock

    async def write(self, command: TaskWrite) -> TaskRef:
        fingerprint = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Task clock must be timezone-aware")
        try:
            async with self._database.session() as session, session.begin():
                replay = await session.get(ConversationTaskOperationRecord, command.operation_id)
                if replay is not None:
                    if replay.command_hash != fingerprint:
                        raise TaskConflict("Operation identity was reused with different data")
                    return TaskRef.model_validate_json(replay.result_json)
                source_ids, attachments = await validate_sources(session, command)
                evidence = TaskEvidence(
                    source_item_id=command.source_item_id,
                    excerpt=command.evidence_excerpt,
                    interpretation=command.interpretation,
                )
                if command.task_id is None:
                    row = ConversationTaskRecord(
                        user_id=command.user_id,
                        thread_id=command.thread_id,
                        revision=1,
                        status="open",
                        objective=command.objective,
                        unresolved_json=json.dumps(command.unresolved, ensure_ascii=False),
                        evidence_json=json.dumps([evidence.model_dump()], ensure_ascii=False),
                        source_item_ids_json=json.dumps(source_ids),
                        attachment_ids_json=json.dumps(attachments),
                        source_turn_id=command.source_turn_id,
                        created_at=now,
                        expires_at=now + self._ttl,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    existing = await session.get(ConversationTaskRecord, command.task_id)
                    if (
                        existing is None
                        or existing.user_id != command.user_id
                        or existing.thread_id != command.thread_id
                    ):
                        raise TaskError("Task is not visible in this conversation")
                    row = existing
                    if row.status != "open" or as_utc(row.expires_at) <= now:
                        raise TaskConflict(
                            "Task is closed or expired; it cannot be silently reopened"
                        )
                    prior = self._ref(row)
                    result = await session.execute(
                        update(ConversationTaskRecord)
                        .where(
                            ConversationTaskRecord.id == row.id,
                            ConversationTaskRecord.revision == command.expected_revision,
                            ConversationTaskRecord.status == "open",
                        )
                        .values(
                            revision=ConversationTaskRecord.revision + 1,
                            objective=command.objective,
                            status=command.status,
                            unresolved_json=json.dumps(command.unresolved, ensure_ascii=False),
                            evidence_json=json.dumps(
                                [entry.model_dump() for entry in (*prior.evidence, evidence)],
                                ensure_ascii=False,
                            ),
                            source_item_ids_json=json.dumps(
                                tuple(dict.fromkeys((*prior.source_item_ids, *source_ids)))
                            ),
                            attachment_ids_json=json.dumps(
                                tuple(dict.fromkeys((*prior.attachment_ids, *attachments)))
                            ),
                            expires_at=now + self._ttl,
                            resolved_at=now if command.status != "open" else None,
                        )
                    )
                    if cast(CursorResult[Any], result).rowcount != 1:
                        raise TaskConflict("Task revision changed; reload before updating")
                    await session.refresh(row)
                ref = self._ref(row)
                session.add(
                    ConversationTaskOperationRecord(
                        operation_id=command.operation_id,
                        task_id=ref.id,
                        command_hash=fingerprint,
                        result_json=ref.model_dump_json(),
                        created_at=now,
                    )
                )
                return ref
        except IntegrityError as exc:
            # A concurrent identical request can safely return its original snapshot.
            async with self._database.session() as session:
                replay = await session.get(ConversationTaskOperationRecord, command.operation_id)
                if replay is not None and replay.command_hash == fingerprint:
                    return TaskRef.model_validate_json(replay.result_json)
            raise TaskConflict("Task write conflicted; reload current task state") from exc

    async def get(self, task_id: str, *, user_id: str, thread_id: str) -> TaskRef:
        async with self._database.session() as session:
            row = await session.get(ConversationTaskRecord, task_id)
            if row is None or row.user_id != user_id or row.thread_id != thread_id:
                raise TaskError("Task is not visible in this conversation")
            ref = self._ref(row)
            if ref.status == "open" and ref.expires_at <= self._clock():
                return ref.model_copy(update={"status": "expired"})
            return ref

    async def list_for_user(
        self,
        user_id: str,
        *,
        at: datetime,
        status: TaskStatus = "open",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[TaskRef, ...]:
        if at.utcoffset() is None or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid task list bounds/time")
        condition = ConversationTaskRecord.status == status
        if status == "open":
            condition = condition & (ConversationTaskRecord.expires_at > at)
        elif status == "expired":
            condition = condition | (
                (ConversationTaskRecord.status == "open")
                & (ConversationTaskRecord.expires_at <= at)
            )
        async with self._database.session() as session:
            rows = await session.scalars(
                select(ConversationTaskRecord)
                .where(
                    ConversationTaskRecord.user_id == user_id,
                    condition,
                )
                .order_by(ConversationTaskRecord.created_at, ConversationTaskRecord.id)
                .limit(limit)
                .offset(offset)
            )
            return tuple(
                self._ref(row).model_copy(update={"status": "expired"})
                if status == "expired"
                else self._ref(row)
                for row in rows
            )

    async def expire_due(self, *, at: datetime) -> int:
        if at.utcoffset() is None:
            raise ValueError("Task expiry time must be timezone-aware")
        async with self._database.session() as session, session.begin():
            result = await session.execute(
                update(ConversationTaskRecord)
                .where(
                    ConversationTaskRecord.status == "open",
                    ConversationTaskRecord.expires_at <= at,
                )
                .values(
                    status="expired", resolved_at=at, revision=ConversationTaskRecord.revision + 1
                )
            )
            return cast(CursorResult[Any], result).rowcount

    async def read_sources(self, task: TaskRef, *, offset: int) -> tuple[dict[str, Any], ...]:
        if offset < 0:
            raise ValueError("Source offset must be nonnegative")
        async with self._database.session() as session:
            return await read_task_sources(session, task, offset=offset)

    @staticmethod
    def _ref(row: ConversationTaskRecord) -> TaskRef:
        return TaskRef(
            id=row.id,
            user_id=row.user_id,
            thread_id=row.thread_id,
            revision=row.revision,
            status=cast(TaskStatus, row.status),
            objective=row.objective,
            unresolved=tuple(json.loads(row.unresolved_json)),
            evidence=tuple(TaskEvidence.model_validate(e) for e in json.loads(row.evidence_json)),
            source_item_ids=tuple(json.loads(row.source_item_ids_json)),
            attachment_ids=tuple(json.loads(row.attachment_ids_json)),
            source_turn_id=row.source_turn_id,
            created_at=as_utc(row.created_at),
            expires_at=as_utc(row.expires_at),
            resolved_at=as_utc(row.resolved_at) if row.resolved_at else None,
        )
