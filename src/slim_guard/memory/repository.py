from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    MemoryBulkOperationRecord,
    MemoryIndexOutboxRecord,
    UserMemoryEventRecord,
    UserMemoryFactRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.domain.source import validate_record_source
from slim_guard.memory.contracts import (
    MemoryBulkRevokeCommand,
    MemoryBulkRevokeResult,
    MemoryChangeAction,
    MemoryFactRef,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryRevokeResult,
    MemoryStatus,
    MemoryWriteChange,
    MemoryWriteCommand,
    MemoryWriteResult,
)
from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemoryNotFound,
    MemorySourceMismatch,
    MemoryStaleEvidence,
)
from slim_guard.memory.registry import CanonicalMemory, MemorySchemaRegistry

MEMORY_POLICY_VERSION = "profile-goal-constraint-handoff-receipt-privacy-v9"


class MemoryRepository:
    """User-isolated persistence for versioned, source-bound profile facts."""

    def __init__(
        self,
        database: Database,
        *,
        registry: MemorySchemaRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        index_sync_enabled: bool = False,
    ) -> None:
        self._database = database
        self._registry = registry or MemorySchemaRegistry()
        self._clock = clock or utc_now
        self._index_sync_enabled = index_sync_enabled

    async def write(self, command: MemoryWriteCommand) -> MemoryWriteResult:
        canonical = tuple(
            self._registry.canonicalize(fact.key, fact.value) for fact in command.facts
        )
        if len({fact.slot_key for fact in canonical}) != len(canonical):
            raise MemoryCollision("Memory write contains duplicate conflict slots")
        async with self._database.session() as session:
            try:
                async with session.begin():
                    evidence = await self._validate_evidence(session, command)
                    existing_operation = tuple(
                        await session.scalars(
                            select(UserMemoryFactRecord).where(
                                UserMemoryFactRecord.user_id == command.user_id,
                                UserMemoryFactRecord.operation_id == command.operation_id
                            )
                        )
                    )
                    if existing_operation:
                        return self._replayed_write(existing_operation, canonical)

                    active_by_slot = {
                        row.slot_key: row
                        for row in await session.scalars(
                            select(UserMemoryFactRecord).where(
                                UserMemoryFactRecord.user_id == command.user_id,
                                UserMemoryFactRecord.slot_key.in_(
                                    tuple(fact.slot_key for fact in canonical)
                                ),
                                UserMemoryFactRecord.status == MemoryStatus.ACTIVE.value,
                            )
                        )
                    }
                    if all(
                        (active := active_by_slot.get(fact.slot_key)) is not None
                        and active.value_hash == fact.value_hash
                        for fact in canonical
                    ):
                        unchanged_refs = tuple(
                            self._ref(active_by_slot[fact.slot_key])
                            for fact in canonical
                        )
                        return MemoryWriteResult(
                            facts=unchanged_refs,
                            changes=tuple(
                                self._change(
                                    action=MemoryChangeAction.UNCHANGED,
                                    current=fact,
                                    previous=fact,
                                )
                                for fact in unchanged_refs
                            ),
                            created_count=0,
                        )

                    now = self._now()
                    output: list[MemoryFactRef] = []
                    changes: list[MemoryWriteChange] = []
                    created_count = 0
                    for fact in canonical:
                        active = await session.scalar(
                            select(UserMemoryFactRecord).where(
                                UserMemoryFactRecord.user_id == command.user_id,
                                UserMemoryFactRecord.slot_key == fact.slot_key,
                                UserMemoryFactRecord.status == MemoryStatus.ACTIVE.value,
                            )
                        )
                        if active is not None and active.value_hash == fact.value_hash:
                            unchanged_ref = self._ref(active)
                            output.append(unchanged_ref)
                            changes.append(
                                self._change(
                                    action=MemoryChangeAction.UNCHANGED,
                                    current=unchanged_ref,
                                    previous=unchanged_ref,
                                )
                            )
                            continue
                        previous = self._ref(active) if active is not None else None
                        if active is not None:
                            await self._reject_older_conflicting_evidence(
                                session,
                                active=active,
                                evidence=evidence,
                            )
                            active.status = MemoryStatus.SUPERSEDED.value
                            active.ended_at = now
                            session.add(
                                self._event(
                                    active,
                                    event_type="superseded",
                                    turn_id=command.source_turn_id,
                                    item_id=command.source_item_id,
                                    detail={"replacement_operation_id": command.operation_id},
                                )
                            )
                            self._enqueue_index(
                                session,
                                row=active,
                                operation="delete",
                                now=now,
                            )
                            await session.flush()
                        row = UserMemoryFactRecord(
                            user_id=command.user_id,
                            kind=fact.spec.kind.value,
                            memory_key=fact.spec.key.value,
                            slot_key=fact.slot_key,
                            value_json=fact.value_json,
                            value_hash=fact.value_hash,
                            status=MemoryStatus.ACTIVE.value,
                            assertion=command.assertion.value,
                            sensitivity=fact.spec.sensitivity.value,
                            operation_id=command.operation_id,
                            supersedes_id=active.id if active is not None else None,
                            source_turn_id=command.source_turn_id,
                            source_item_id=command.source_item_id,
                            evidence_item_id=evidence.id,
                            source_tool_call_id=command.source_tool_call_id,
                            valid_from=now,
                            review_after=(
                                now + timedelta(days=fact.spec.review_days)
                                if fact.spec.review_days is not None
                                else None
                            ),
                        )
                        session.add(row)
                        await session.flush()
                        session.add(
                            self._event(
                                row,
                                event_type="created",
                                turn_id=command.source_turn_id,
                                item_id=command.source_item_id,
                                detail={"schema_version": self._registry.version},
                            )
                        )
                        self._enqueue_index(
                            session,
                            row=row,
                            operation="upsert",
                            now=now,
                        )
                        current = self._ref(row)
                        output.append(current)
                        changes.append(
                            self._change(
                                action=(
                                    MemoryChangeAction.UPDATED
                                    if previous is not None
                                    else MemoryChangeAction.CREATED
                                ),
                                current=current,
                                previous=previous,
                            )
                        )
                        created_count += 1
                    return MemoryWriteResult(
                        facts=tuple(output),
                        changes=tuple(changes),
                        created_count=created_count,
                    )
            except IntegrityError as exc:
                raise MemoryCollision("Memory write conflicted with another operation") from exc

    async def active(
        self,
        user_id: str,
        *,
        key: MemoryKey | None = None,
        limit: int = 30,
    ) -> tuple[MemoryFactRef, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Memory fact limit must be between 1 and 100")
        filters = [
            UserMemoryFactRecord.user_id == user_id,
            UserMemoryFactRecord.status == MemoryStatus.ACTIVE.value,
        ]
        if key is not None:
            filters.append(UserMemoryFactRecord.memory_key == key.value)
        async with self._database.session() as session:
            rows = await session.scalars(
                select(UserMemoryFactRecord)
                .where(*filters)
                .order_by(
                    UserMemoryFactRecord.memory_key,
                    UserMemoryFactRecord.created_at.desc(),
                    UserMemoryFactRecord.id,
                )
                .limit(limit)
            )
            return tuple(self._ref(row) for row in rows)

    async def revoke(self, command: MemoryRevokeCommand) -> MemoryRevokeResult:
        async with self._database.session() as session, session.begin():
            mismatch = await validate_record_source(
                session,
                user_id=command.user_id,
                source_turn_id=command.source_turn_id,
                source_item_id=command.source_item_id,
            )
            if mismatch is not None:
                raise MemorySourceMismatch(f"Memory {mismatch}")
            row = await session.scalar(
                select(UserMemoryFactRecord).where(
                    UserMemoryFactRecord.id == command.memory_id,
                    UserMemoryFactRecord.user_id == command.user_id,
                )
            )
            if row is None:
                raise MemoryNotFound("Memory fact is not visible to the current user")
            if row.status != MemoryStatus.ACTIVE.value:
                return MemoryRevokeResult(fact=self._ref(row), changed=False)
            row.status = MemoryStatus.REVOKED.value
            row.ended_at = self._now()
            session.add(
                self._event(
                    row,
                    event_type="revoked",
                    turn_id=command.source_turn_id,
                    item_id=command.source_item_id,
                    detail={"operation_id": command.operation_id},
                )
            )
            self._enqueue_index(
                session,
                row=row,
                operation="delete",
                now=row.ended_at,
            )
            await session.flush()
            return MemoryRevokeResult(fact=self._ref(row), changed=True)

    async def revoke_all(
        self,
        command: MemoryBulkRevokeCommand,
    ) -> MemoryBulkRevokeResult:
        async with self._database.session() as session, session.begin():
            await self._validate_bulk_evidence(session, command)
            replay = await session.get(MemoryBulkOperationRecord, command.operation_id)
            if replay is not None:
                return self._replayed_bulk_revoke(replay, command)
            rows = tuple(
                await session.scalars(
                    select(UserMemoryFactRecord)
                    .where(
                        UserMemoryFactRecord.user_id == command.user_id,
                        UserMemoryFactRecord.status == MemoryStatus.ACTIVE.value,
                    )
                    .order_by(UserMemoryFactRecord.id)
                )
            )
            now = self._now()
            memory_ids = tuple(row.id for row in rows)
            for row in rows:
                row.status = MemoryStatus.REVOKED.value
                row.ended_at = now
                session.add(
                    self._event(
                        row,
                        event_type="revoked",
                        turn_id=command.source_turn_id,
                        item_id=command.source_item_id,
                        detail={
                            "operation_id": command.operation_id,
                            "scope": command.scope,
                        },
                    )
                )
            session.add(
                MemoryBulkOperationRecord(
                    operation_id=command.operation_id,
                    user_id=command.user_id,
                    scope=command.scope,
                    memory_ids_json=json.dumps(memory_ids, separators=(",", ":")),
                    revoked_count=len(memory_ids),
                    source_turn_id=command.source_turn_id,
                    source_item_id=command.source_item_id,
                    source_tool_call_id=command.source_tool_call_id,
                    created_at=now,
                )
            )
            if self._index_sync_enabled:
                session.add(
                    MemoryIndexOutboxRecord(
                        operation_key=f"delete_user:{command.operation_id}",
                        user_id=command.user_id,
                        memory_id=None,
                        operation="delete_user",
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            try:
                await session.flush()
            except IntegrityError as exc:
                raise MemoryCollision(
                    "Bulk memory revocation conflicted with another operation"
                ) from exc
            return MemoryBulkRevokeResult(
                scope=command.scope,
                memory_ids=memory_ids,
                revoked_count=len(memory_ids),
            )

    async def _validate_evidence(
        self,
        session: AsyncSession,
        command: MemoryWriteCommand,
    ) -> AgentItemRecord:
        mismatch = await validate_record_source(
            session,
            user_id=command.user_id,
            source_turn_id=command.source_turn_id,
            source_item_id=command.source_item_id,
        )
        if mismatch is not None:
            raise MemorySourceMismatch(f"Memory {mismatch}")
        action_source = await session.get(AgentItemRecord, command.source_item_id)
        if action_source is None or action_source.item_type != "user_message":
            raise MemoryEvidenceMismatch("Memory writes require a current user message source")
        evidence_item_id = command.evidence_item_id or command.source_item_id
        evidence = await session.get(AgentItemRecord, evidence_item_id)
        if (
            evidence is None
            or evidence.item_type != "user_message"
            or evidence.status != "completed"
        ):
            raise MemoryEvidenceMismatch(
                "Memory evidence must reference a completed user message"
            )
        evidence_mismatch = await validate_record_source(
            session,
            user_id=command.user_id,
            source_turn_id=evidence.turn_id,
            source_item_id=evidence.id,
        )
        if evidence_mismatch is not None:
            raise MemorySourceMismatch(f"Memory evidence {evidence_mismatch}")
        if self._as_utc(evidence.created_at) > self._as_utc(action_source.created_at):
            raise MemoryEvidenceMismatch("Memory evidence cannot come from a future message")
        try:
            payload = json.loads(evidence.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryEvidenceMismatch("Memory source payload is invalid") from exc
        text = payload.get("text")
        if not isinstance(text, str) or command.evidence_excerpt not in text:
            raise MemoryEvidenceMismatch(
                "Memory evidence must be an exact excerpt of its referenced user message"
            )
        return evidence

    async def _reject_older_conflicting_evidence(
        self,
        session: AsyncSession,
        *,
        active: UserMemoryFactRecord,
        evidence: AgentItemRecord,
    ) -> None:
        active_evidence = await session.get(
            AgentItemRecord,
            active.evidence_item_id or active.source_item_id,
        )
        if active_evidence is None:
            raise MemorySourceMismatch("Active memory evidence no longer exists")
        if self._as_utc(evidence.created_at) < self._as_utc(active_evidence.created_at):
            raise MemoryStaleEvidence(
                "Historical evidence is older than the active conflicting memory"
            )

    async def _validate_bulk_evidence(
        self,
        session: AsyncSession,
        command: MemoryBulkRevokeCommand,
    ) -> None:
        mismatch = await validate_record_source(
            session,
            user_id=command.user_id,
            source_turn_id=command.source_turn_id,
            source_item_id=command.source_item_id,
        )
        if mismatch is not None:
            raise MemorySourceMismatch(f"Bulk memory {mismatch}")
        source = await session.get(AgentItemRecord, command.source_item_id)
        if source is None or source.item_type != "user_message":
            raise MemoryEvidenceMismatch(
                "Bulk memory revocation requires a current user message"
            )
        try:
            payload = json.loads(source.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryEvidenceMismatch("Bulk memory source payload is invalid") from exc
        text = payload.get("text")
        if not isinstance(text, str) or command.evidence_excerpt not in text:
            raise MemoryEvidenceMismatch(
                "Bulk memory evidence must be an exact excerpt of the current user message"
            )

    @staticmethod
    def _replayed_bulk_revoke(
        row: MemoryBulkOperationRecord,
        command: MemoryBulkRevokeCommand,
    ) -> MemoryBulkRevokeResult:
        if (
            row.user_id != command.user_id
            or row.scope != command.scope
            or row.source_turn_id != command.source_turn_id
            or row.source_item_id != command.source_item_id
            or row.source_tool_call_id != command.source_tool_call_id
        ):
            raise MemoryCollision(
                "Bulk memory operation identity was reused with different data"
            )
        try:
            memory_ids = json.loads(row.memory_ids_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryCollision("Bulk memory operation payload is invalid") from exc
        if not isinstance(memory_ids, list) or not all(
            isinstance(memory_id, str) for memory_id in memory_ids
        ):
            raise MemoryCollision("Bulk memory operation IDs are invalid")
        return MemoryBulkRevokeResult(
            scope=row.scope,
            memory_ids=tuple(memory_ids),
            revoked_count=row.revoked_count,
        )

    def _replayed_write(
        self,
        rows: tuple[UserMemoryFactRecord, ...],
        expected: tuple[CanonicalMemory, ...],
    ) -> MemoryWriteResult:
        actual = {(row.slot_key, row.value_hash) for row in rows}
        wanted = {(fact.slot_key, fact.value_hash) for fact in expected}
        if actual != wanted:
            raise MemoryCollision("Memory operation identity was reused with different facts")
        return MemoryWriteResult(
            facts=tuple(self._ref(row) for row in rows),
            changes=tuple(
                self._change(
                    action=MemoryChangeAction.UNCHANGED,
                    current=self._ref(row),
                    previous=self._ref(row),
                )
                for row in rows
            ),
            created_count=0,
        )

    @staticmethod
    def _change(
        *,
        action: MemoryChangeAction,
        current: MemoryFactRef,
        previous: MemoryFactRef | None,
    ) -> MemoryWriteChange:
        return MemoryWriteChange(
            action=action,
            memory_id=current.id,
            key=current.key,
            slot_key=current.slot_key,
            previous_memory_id=previous.id if previous is not None else None,
            previous_value=previous.value if previous is not None else None,
            current_value=current.value,
        )

    @staticmethod
    def _event(
        row: UserMemoryFactRecord,
        *,
        event_type: str,
        turn_id: str,
        item_id: str,
        detail: dict[str, Any],
    ) -> UserMemoryEventRecord:
        return UserMemoryEventRecord(
            memory_id=row.id,
            user_id=row.user_id,
            event_type=event_type,
            turn_id=turn_id,
            item_id=item_id,
            policy_version=MEMORY_POLICY_VERSION,
            detail_json=json.dumps(detail, separators=(",", ":"), sort_keys=True),
        )

    def _enqueue_index(
        self,
        session: AsyncSession,
        *,
        row: UserMemoryFactRecord,
        operation: str,
        now: datetime | None,
    ) -> None:
        if not self._index_sync_enabled:
            return
        queued_at = now or self._now()
        session.add(
            MemoryIndexOutboxRecord(
                operation_key=f"{operation}:{row.id}:{row.value_hash}",
                user_id=row.user_id,
                memory_id=row.id,
                operation=operation,
                status="pending",
                available_at=queued_at,
                created_at=queued_at,
                updated_at=queued_at,
            )
        )

    @classmethod
    def _ref(cls, row: UserMemoryFactRecord) -> MemoryFactRef:
        if row.value_json is None:
            value: dict[str, Any] = {}
        else:
            loaded = json.loads(row.value_json)
            if not isinstance(loaded, dict):
                raise MemoryCollision(f"Memory value is not an object: {row.id}")
            value = loaded
        return MemoryFactRef(
            id=row.id,
            user_id=row.user_id,
            kind=row.kind,
            key=row.memory_key,
            slot_key=row.slot_key,
            value=value,
            value_hash=row.value_hash,
            status=row.status,
            assertion=row.assertion,
            sensitivity=row.sensitivity,
            supersedes_id=row.supersedes_id,
            source_turn_id=row.source_turn_id,
            source_item_id=row.source_item_id,
            evidence_item_id=row.evidence_item_id or row.source_item_id,
            source_tool_call_id=row.source_tool_call_id,
            valid_from=cls._as_utc(row.valid_from),
            review_after=(
                cls._as_utc(row.review_after)
                if row.review_after is not None
                else None
            ),
            created_at=cls._as_utc(row.created_at),
            ended_at=cls._as_utc(row.ended_at) if row.ended_at is not None else None,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Memory clock must be timezone-aware")
        return value.astimezone(UTC)
