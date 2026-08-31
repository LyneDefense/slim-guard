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
    UserMemoryEventRecord,
    UserMemoryFactRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.domain.source import validate_record_source
from slim_guard.memory.contracts import (
    MemoryBulkRevokeCommand,
    MemoryBulkRevokeResult,
    MemoryFactRef,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryRevokeResult,
    MemoryStatus,
    MemoryWriteCommand,
    MemoryWriteResult,
)
from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemoryNotFound,
    MemorySourceMismatch,
)
from slim_guard.memory.registry import CanonicalMemory, MemorySchemaRegistry

MEMORY_POLICY_VERSION = "profile-goal-constraint-handoff-privacy-v5"


class MemoryRepository:
    """User-isolated persistence for versioned, source-bound profile facts."""

    def __init__(
        self,
        database: Database,
        *,
        registry: MemorySchemaRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._registry = registry or MemorySchemaRegistry()
        self._clock = clock or utc_now

    async def write(self, command: MemoryWriteCommand) -> MemoryWriteResult:
        canonical = tuple(
            self._registry.canonicalize(fact.key, fact.value) for fact in command.facts
        )
        if len({fact.slot_key for fact in canonical}) != len(canonical):
            raise MemoryCollision("Memory write contains duplicate conflict slots")
        async with self._database.session() as session:
            try:
                async with session.begin():
                    await self._validate_evidence(session, command)
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

                    now = self._now()
                    output: list[MemoryFactRef] = []
                    created_count = 0
                    for fact in canonical:
                        active = await session.scalar(
                            select(UserMemoryFactRecord).where(
                                UserMemoryFactRecord.user_id == command.user_id,
                                UserMemoryFactRecord.slot_key == fact.slot_key,
                                UserMemoryFactRecord.status == MemoryStatus.ACTIVE.value,
                            )
                        )
                        if active is not None:
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
                        output.append(self._ref(row))
                        created_count += 1
                    return MemoryWriteResult(
                        facts=tuple(output),
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
    ) -> None:
        mismatch = await validate_record_source(
            session,
            user_id=command.user_id,
            source_turn_id=command.source_turn_id,
            source_item_id=command.source_item_id,
        )
        if mismatch is not None:
            raise MemorySourceMismatch(f"Memory {mismatch}")
        source = await session.get(AgentItemRecord, command.source_item_id)
        if source is None or source.item_type != "user_message":
            raise MemoryEvidenceMismatch("Memory writes require a current user message source")
        try:
            payload = json.loads(source.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryEvidenceMismatch("Memory source payload is invalid") from exc
        text = payload.get("text")
        if not isinstance(text, str) or command.evidence_excerpt not in text:
            raise MemoryEvidenceMismatch(
                "Memory evidence must be an exact excerpt of the current user message"
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
            created_count=0,
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
