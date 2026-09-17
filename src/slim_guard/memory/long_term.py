from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    LongTermMemoryEventRecord,
    LongTermMemoryIndexOutboxRecord,
    UserLongTermMemoryRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.domain.source import validate_record_source
from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemoryNotFound,
    MemorySourceMismatch,
)

LONG_TERM_MEMORY_POLICY_VERSION = "conversational-long-term-memory-v1"


class LongTermMemoryDurability(StrEnum):
    LONG_TERM = "long_term"
    TEMPORARY = "temporary"


class LongTermMemoryOperation(StrEnum):
    CREATE = "create"
    SUPERSEDE = "supersede"
    NONE = "none"


class LongTermMemorySensitivity(StrEnum):
    NORMAL = "normal"
    HEALTH = "health"
    RESTRICTED = "restricted"


class LongTermMemoryCandidate(BaseModel):
    """One evidence-bound proposal produced by the asynchronous extractor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_text: str = Field(min_length=1, max_length=500)
    category: str = Field(default="other", min_length=1, max_length=64)
    durability: LongTermMemoryDurability = LongTermMemoryDurability.LONG_TERM
    expires_at: datetime | None = None
    sensitivity: LongTermMemorySensitivity = LongTermMemorySensitivity.NORMAL
    evidence_ref: str = Field(min_length=1, max_length=128)
    evidence_excerpt: str = Field(min_length=1, max_length=512)
    operation: LongTermMemoryOperation = LongTermMemoryOperation.CREATE
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("content_text", "evidence_excerpt")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Long-term memory text cannot be blank")
        return normalized

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if not normalized:
            raise ValueError("Long-term memory category cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_operation_and_expiry(self) -> LongTermMemoryCandidate:
        if self.durability is LongTermMemoryDurability.TEMPORARY:
            if self.expires_at is None:
                raise ValueError("Temporary long-term memory requires expires_at")
            if self.expires_at.utcoffset() is None:
                raise ValueError("Long-term memory expiry must be timezone-aware")
        if self.operation is LongTermMemoryOperation.SUPERSEDE:
            if self.supersedes_memory_id is None:
                raise ValueError("Supersede requires supersedes_memory_id")
        elif self.supersedes_memory_id is not None:
            raise ValueError("Only supersede may include supersedes_memory_id")
        return self


class LongTermMemoryRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    content_text: str
    category: str
    durability: LongTermMemoryDurability
    status: str
    sensitivity: LongTermMemorySensitivity
    supersedes_id: str | None
    source_turn_id: str
    source_item_id: str
    valid_from: datetime
    expires_at: datetime | None
    review_after: datetime | None
    created_at: datetime
    ended_at: datetime | None


class LongTermMemoryWriteAction(StrEnum):
    CREATED = "created"
    SUPERSEDED = "superseded"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


class LongTermMemoryWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: LongTermMemoryWriteAction
    memory: LongTermMemoryRef | None = None
    previous_memory_id: str | None = None


class LongTermMemoryRepository:
    """PostgreSQL authority for open-text conversational memories."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
        index_sync_enabled: bool = False,
    ) -> None:
        self._database = database
        self._clock = clock or utc_now
        self._index_sync_enabled = index_sync_enabled

    async def active(
        self,
        user_id: str,
        *,
        memory_ids: Sequence[str] | None = None,
        limit: int = 100,
    ) -> tuple[LongTermMemoryRef, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Long-term memory limit must be between 1 and 200")
        now = self._now()
        filters = [
            UserLongTermMemoryRecord.user_id == user_id,
            UserLongTermMemoryRecord.status == "active",
            or_(
                UserLongTermMemoryRecord.expires_at.is_(None),
                UserLongTermMemoryRecord.expires_at > now,
            ),
        ]
        if memory_ids is not None:
            if not memory_ids:
                return ()
            filters.append(UserLongTermMemoryRecord.id.in_(tuple(memory_ids)))
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(UserLongTermMemoryRecord)
                    .where(*filters)
                    .order_by(
                        UserLongTermMemoryRecord.created_at.desc(),
                        UserLongTermMemoryRecord.id,
                    )
                    .limit(limit)
                )
            )
        return tuple(self._ref(row) for row in rows)

    async def apply(
        self,
        *,
        user_id: str,
        source_turn_id: str,
        source_item_id: str,
        operation_prefix: str,
        candidates: Sequence[LongTermMemoryCandidate],
    ) -> tuple[LongTermMemoryWriteResult, ...]:
        if len(candidates) > 8:
            raise ValueError("At most 8 long-term memories may be written per extraction")
        async with self._database.session() as session:
            try:
                async with session.begin():
                    evidence_text = await self._validate_source(
                        session,
                        user_id=user_id,
                        source_turn_id=source_turn_id,
                        source_item_id=source_item_id,
                    )
                    now = self._now()
                    results: list[LongTermMemoryWriteResult] = []
                    for index, candidate in enumerate(candidates):
                        operation_id = f"{operation_prefix}:{index}"
                        replay = await session.scalar(
                            select(UserLongTermMemoryRecord).where(
                                UserLongTermMemoryRecord.user_id == user_id,
                                UserLongTermMemoryRecord.operation_id == operation_id,
                            )
                        )
                        if replay is not None:
                            results.append(
                                LongTermMemoryWriteResult(
                                    action=LongTermMemoryWriteAction.UNCHANGED,
                                    memory=self._ref(replay),
                                    previous_memory_id=replay.supersedes_id,
                                )
                            )
                            continue
                        result = await self._apply_one(
                            session,
                            user_id=user_id,
                            source_turn_id=source_turn_id,
                            source_item_id=source_item_id,
                            evidence_text=evidence_text,
                            operation_id=operation_id,
                            candidate=candidate,
                            now=now,
                        )
                        results.append(result)
                    return tuple(results)
            except IntegrityError as exc:
                raise MemoryCollision(
                    "Long-term memory write conflicted with another operation"
                ) from exc

    async def revoke(
        self,
        *,
        user_id: str,
        memory_id: str,
        source_turn_id: str,
        source_item_id: str,
        operation_id: str,
    ) -> tuple[LongTermMemoryRef, bool]:
        async with self._database.session() as session, session.begin():
            await self._validate_source(
                session,
                user_id=user_id,
                source_turn_id=source_turn_id,
                source_item_id=source_item_id,
            )
            row = await session.scalar(
                select(UserLongTermMemoryRecord).where(
                    UserLongTermMemoryRecord.id == memory_id,
                    UserLongTermMemoryRecord.user_id == user_id,
                )
            )
            if row is None:
                raise MemoryNotFound("Long-term memory is not visible to the current user")
            if row.status != "active":
                return self._ref(row), False
            now = self._now()
            row.status = "revoked"
            row.ended_at = now
            session.add(
                self._event(
                    row,
                    event_type="revoked",
                    turn_id=source_turn_id,
                    item_id=source_item_id,
                    detail={"operation_id": operation_id},
                    now=now,
                )
            )
            self._enqueue_index(session, row=row, operation="delete", now=now)
            await session.flush()
            return self._ref(row), True

    async def _apply_one(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        source_turn_id: str,
        source_item_id: str,
        evidence_text: str,
        operation_id: str,
        candidate: LongTermMemoryCandidate,
        now: datetime,
    ) -> LongTermMemoryWriteResult:
        if candidate.operation is LongTermMemoryOperation.NONE:
            return LongTermMemoryWriteResult(action=LongTermMemoryWriteAction.SKIPPED)
        if candidate.evidence_ref != source_item_id:
            raise MemoryEvidenceMismatch(
                "Long-term memory evidence_ref must be the current user message"
            )
        if candidate.evidence_excerpt not in evidence_text:
            raise MemoryEvidenceMismatch(
                "Long-term memory evidence must be an exact current-message excerpt"
            )
        if candidate.sensitivity is LongTermMemorySensitivity.RESTRICTED:
            return LongTermMemoryWriteResult(action=LongTermMemoryWriteAction.SKIPPED)
        expires_at = self._aware_optional(candidate.expires_at)
        if expires_at is not None and expires_at <= now:
            return LongTermMemoryWriteResult(action=LongTermMemoryWriteAction.SKIPPED)

        content_hash = self._sha256(candidate.content_text)
        duplicate = await session.scalar(
            select(UserLongTermMemoryRecord).where(
                UserLongTermMemoryRecord.user_id == user_id,
                UserLongTermMemoryRecord.status == "active",
                UserLongTermMemoryRecord.content_hash == content_hash,
            )
        )
        if duplicate is not None:
            return LongTermMemoryWriteResult(
                action=LongTermMemoryWriteAction.UNCHANGED,
                memory=self._ref(duplicate),
            )

        previous: UserLongTermMemoryRecord | None = None
        if candidate.operation is LongTermMemoryOperation.SUPERSEDE:
            previous = await session.scalar(
                select(UserLongTermMemoryRecord).where(
                    UserLongTermMemoryRecord.id == candidate.supersedes_memory_id,
                    UserLongTermMemoryRecord.user_id == user_id,
                    UserLongTermMemoryRecord.status == "active",
                )
            )
            if previous is None:
                raise MemoryNotFound("Superseded long-term memory is not active")
            previous.status = "superseded"
            previous.ended_at = now
            session.add(
                self._event(
                    previous,
                    event_type="superseded",
                    turn_id=source_turn_id,
                    item_id=source_item_id,
                    detail={"replacement_operation_id": operation_id},
                    now=now,
                )
            )
            self._enqueue_index(session, row=previous, operation="delete", now=now)
            await session.flush()

        row = UserLongTermMemoryRecord(
            user_id=user_id,
            content_text=candidate.content_text,
            category=candidate.category,
            durability=candidate.durability.value,
            status="active",
            sensitivity=candidate.sensitivity.value,
            operation_id=operation_id,
            supersedes_id=previous.id if previous is not None else None,
            source_turn_id=source_turn_id,
            source_item_id=source_item_id,
            evidence_excerpt_hash=self._sha256(candidate.evidence_excerpt),
            content_hash=content_hash,
            valid_from=now,
            expires_at=expires_at,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        session.add(
            self._event(
                row,
                event_type="created",
                turn_id=source_turn_id,
                item_id=source_item_id,
                detail={"category": candidate.category},
                now=now,
            )
        )
        self._enqueue_index(session, row=row, operation="upsert", now=now)
        return LongTermMemoryWriteResult(
            action=(
                LongTermMemoryWriteAction.SUPERSEDED
                if previous is not None
                else LongTermMemoryWriteAction.CREATED
            ),
            memory=self._ref(row),
            previous_memory_id=previous.id if previous is not None else None,
        )

    async def _validate_source(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        source_turn_id: str,
        source_item_id: str,
    ) -> str:
        mismatch = await validate_record_source(
            session,
            user_id=user_id,
            source_turn_id=source_turn_id,
            source_item_id=source_item_id,
        )
        if mismatch is not None:
            raise MemorySourceMismatch(f"Long-term memory {mismatch}")
        source = await session.get(AgentItemRecord, source_item_id)
        if source is None or source.item_type != "user_message" or source.status != "completed":
            raise MemoryEvidenceMismatch(
                "Long-term memory requires a completed current user message"
            )
        try:
            payload = json.loads(source.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryEvidenceMismatch("Long-term memory source payload is invalid") from exc
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MemoryEvidenceMismatch("Long-term memory source has no user text")
        return text

    def _enqueue_index(
        self,
        session: AsyncSession,
        *,
        row: UserLongTermMemoryRecord,
        operation: str,
        now: datetime,
    ) -> None:
        if not self._index_sync_enabled:
            return
        version = row.content_hash if operation == "upsert" else row.status
        session.add(
            LongTermMemoryIndexOutboxRecord(
                operation_key=f"{operation}:{row.id}:{version}",
                user_id=row.user_id,
                memory_id=row.id,
                operation=operation,
                status="pending",
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _event(
        row: UserLongTermMemoryRecord,
        *,
        event_type: str,
        turn_id: str,
        item_id: str,
        detail: dict[str, object],
        now: datetime,
    ) -> LongTermMemoryEventRecord:
        return LongTermMemoryEventRecord(
            memory_id=row.id,
            user_id=row.user_id,
            event_type=event_type,
            turn_id=turn_id,
            item_id=item_id,
            policy_version=LONG_TERM_MEMORY_POLICY_VERSION,
            detail_json=json.dumps(
                detail,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            created_at=now,
        )

    @classmethod
    def _ref(cls, row: UserLongTermMemoryRecord) -> LongTermMemoryRef:
        if row.content_text is None:
            raise MemoryNotFound("Long-term memory content has been purged")
        return LongTermMemoryRef(
            id=row.id,
            user_id=row.user_id,
            content_text=row.content_text,
            category=row.category,
            durability=LongTermMemoryDurability(row.durability),
            status=row.status,
            sensitivity=LongTermMemorySensitivity(row.sensitivity),
            supersedes_id=row.supersedes_id,
            source_turn_id=row.source_turn_id,
            source_item_id=row.source_item_id,
            valid_from=cls._as_utc(row.valid_from),
            expires_at=cls._aware_optional(row.expires_at),
            review_after=cls._aware_optional(row.review_after),
            created_at=cls._as_utc(row.created_at),
            ended_at=cls._aware_optional(row.ended_at),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Long-term memory clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)

    @classmethod
    def _aware_optional(cls, value: datetime | None) -> datetime | None:
        return cls._as_utc(value) if value is not None else None


__all__ = [
    "LONG_TERM_MEMORY_POLICY_VERSION",
    "LongTermMemoryCandidate",
    "LongTermMemoryDurability",
    "LongTermMemoryOperation",
    "LongTermMemoryRef",
    "LongTermMemoryRepository",
    "LongTermMemorySensitivity",
    "LongTermMemoryWriteAction",
    "LongTermMemoryWriteResult",
]
