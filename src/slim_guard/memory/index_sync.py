from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import MemoryIndexOutboxRecord, UserMemoryFactRecord, utc_now
from slim_guard.db.session import Database
from slim_guard.memory.engine import MemoryEngine, MemoryEngineError

logger = logging.getLogger(__name__)

_MEMORY_LABELS = {
    "identity.preferred_name": "偏好称呼",
    "profile.height": "身高",
    "profile.exercise_habit": "长期运动习惯",
    "coaching.response_style": "回复风格",
    "food.preference": "饮食偏好",
    "exercise.preference": "运动偏好",
    "goal.target_weight": "目标体重",
    "goal.target_body_fat": "目标体脂率",
    "goal.behavior": "行为目标",
    "constraint.dietary": "饮食限制",
    "constraint.exercise": "运动限制",
    "constraint.health_context": "用户自述健康背景",
}


@dataclass(frozen=True, slots=True)
class MemoryIndexOperation:
    id: str
    user_id: str
    memory_id: str | None
    operation: str
    attempt_count: int


class MemoryIndexSyncRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or utc_now

    async def enqueue_active_backfill(self) -> int:
        """Idempotently queue active facts created before semantic indexing was enabled."""
        now = self._now()
        async with self._database.session() as session, session.begin():
            facts = tuple(
                await session.scalars(
                    select(UserMemoryFactRecord).where(
                        UserMemoryFactRecord.status == "active"
                    )
                )
            )
            operation_keys = {
                f"upsert:{fact.id}:{fact.value_hash}" for fact in facts
            }
            existing = set(
                await session.scalars(
                    select(MemoryIndexOutboxRecord.operation_key).where(
                        MemoryIndexOutboxRecord.operation_key.in_(operation_keys)
                    )
                )
            ) if operation_keys else set()
            queued = 0
            for fact in facts:
                operation_key = f"upsert:{fact.id}:{fact.value_hash}"
                if operation_key in existing:
                    continue
                session.add(
                    MemoryIndexOutboxRecord(
                        operation_key=operation_key,
                        user_id=fact.user_id,
                        memory_id=fact.id,
                        operation="upsert",
                        status="pending",
                        available_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                queued += 1
            return queued

    async def claim(
        self,
        *,
        limit: int,
        lease: timedelta,
    ) -> tuple[MemoryIndexOperation, ...]:
        now = self._now()
        claimed: list[MemoryIndexOperation] = []
        async with self._database.session() as session, session.begin():
            rows = tuple(
                await session.scalars(
                    select(MemoryIndexOutboxRecord)
                    .where(
                        or_(
                            (
                                (MemoryIndexOutboxRecord.status == "pending")
                                & (MemoryIndexOutboxRecord.available_at <= now)
                            ),
                            (
                                (MemoryIndexOutboxRecord.status == "processing")
                                & (MemoryIndexOutboxRecord.lease_until <= now)
                            ),
                        )
                    )
                    .order_by(
                        MemoryIndexOutboxRecord.available_at,
                        MemoryIndexOutboxRecord.created_at,
                    )
                    .limit(limit)
                )
            )
            for row in rows:
                result = await session.execute(
                    update(MemoryIndexOutboxRecord)
                    .where(
                        MemoryIndexOutboxRecord.id == row.id,
                        MemoryIndexOutboxRecord.status == row.status,
                    )
                    .values(
                        status="processing",
                        attempt_count=row.attempt_count + 1,
                        lease_until=now + lease,
                        updated_at=now,
                        error_code=None,
                        error_detail=None,
                    )
                )
                if cast(CursorResult[Any], result).rowcount != 1:
                    continue
                claimed.append(
                    MemoryIndexOperation(
                        id=row.id,
                        user_id=row.user_id,
                        memory_id=row.memory_id,
                        operation=row.operation,
                        attempt_count=row.attempt_count + 1,
                    )
                )
        return tuple(claimed)

    async def fact(self, memory_id: str) -> UserMemoryFactRecord | None:
        async with self._database.session() as session:
            return await session.get(UserMemoryFactRecord, memory_id)

    async def complete(self, operation_id: str) -> None:
        now = self._now()
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(MemoryIndexOutboxRecord)
                .where(
                    MemoryIndexOutboxRecord.id == operation_id,
                    MemoryIndexOutboxRecord.status == "processing",
                )
                .values(
                    status="completed",
                    lease_until=None,
                    completed_at=now,
                    updated_at=now,
                )
            )

    async def fail(
        self,
        operation: MemoryIndexOperation,
        *,
        error: Exception,
        max_attempts: int,
    ) -> None:
        now = self._now()
        terminal = operation.attempt_count >= max_attempts
        retry_seconds = min(300, 2 ** min(operation.attempt_count, 8))
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(MemoryIndexOutboxRecord)
                .where(
                    MemoryIndexOutboxRecord.id == operation.id,
                    MemoryIndexOutboxRecord.status == "processing",
                )
                .values(
                    status="failed" if terminal else "pending",
                    available_at=now + timedelta(seconds=retry_seconds),
                    lease_until=None,
                    error_code=type(error).__name__,
                    error_detail=str(error)[:1000],
                    updated_at=now,
                )
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Memory index sync clock must be timezone-aware")
        return value.astimezone(UTC)


class MemoryIndexSyncService:
    def __init__(
        self,
        *,
        repository: MemoryIndexSyncRepository,
        engine: MemoryEngine,
        interval_seconds: int = 5,
        batch_size: int = 20,
        lease_seconds: int = 60,
        max_attempts: int = 10,
    ) -> None:
        if interval_seconds < 1 or batch_size < 1 or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Memory index sync settings must be positive")
        self._repository = repository
        self._engine = engine
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._lease = timedelta(seconds=lease_seconds)
        self._max_attempts = max_attempts

    async def process_once(self) -> int:
        operations = await self._repository.claim(
            limit=self._batch_size,
            lease=self._lease,
        )
        for operation in operations:
            try:
                await self._execute(operation)
            except (MemoryEngineError, ValueError, TypeError) as exc:
                logger.warning(
                    "memory_index_sync_failed",
                    extra={
                        "operation": operation.operation,
                        "attempt_count": operation.attempt_count,
                        "error_type": type(exc).__name__,
                    },
                )
                await self._repository.fail(
                    operation,
                    error=exc,
                    max_attempts=self._max_attempts,
                )
            else:
                await self._repository.complete(operation.id)
        return len(operations)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = await self.process_once()
            if processed >= self._batch_size:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _execute(self, operation: MemoryIndexOperation) -> None:
        if operation.operation == "delete_user":
            await self._engine.delete_user(user_id=operation.user_id)
            return
        if operation.memory_id is None:
            raise ValueError("Memory index operation is missing memory_id")
        row = await self._repository.fact(operation.memory_id)
        if operation.operation == "delete" or row is None or row.status != "active":
            await self._engine.delete_canonical(
                user_id=operation.user_id,
                memory_id=operation.memory_id,
            )
            return
        value = json.loads(row.value_json or "{}")
        if not isinstance(value, dict):
            raise ValueError("Canonical memory value is not an object")
        label = _MEMORY_LABELS.get(row.memory_key, row.memory_key)
        text = f"{label}：{json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        await self._engine.upsert_canonical(
            user_id=operation.user_id,
            memory_id=row.id,
            value_hash=row.value_hash,
            text=text,
            metadata={
                "memory_key": row.memory_key,
                "kind": row.kind,
                "sensitivity": row.sensitivity,
            },
        )
