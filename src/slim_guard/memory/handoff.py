from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    AgentTurnRecord,
    MemoryHandoffRecord,
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


class HandoffUpsertCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=300)
    unresolved: tuple[str, ...] = Field(min_length=1, max_length=5)
    evidence_excerpt: str = Field(min_length=1, max_length=512)
    operation_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str = Field(min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @field_validator("objective", "evidence_excerpt")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Handoff text cannot be blank")
        return normalized

    @field_validator("unresolved")
    @classmethod
    def normalize_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value or len(value) > 300 for value in normalized):
            raise ValueError("Handoff unresolved items must be 1 to 300 characters")
        return normalized


class HandoffResolveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    handoff_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str = Field(min_length=1, max_length=128)


class HandoffRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    thread_id: str
    status: str
    objective: str
    unresolved: tuple[str, ...]
    source_turn_id: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


class HandoffRepository:
    def __init__(
        self,
        database: Database,
        *,
        ttl: timedelta = timedelta(days=14),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Handoff TTL must be positive")
        self._database = database
        self._ttl = ttl
        self._clock = clock or utc_now

    async def upsert(self, command: HandoffUpsertCommand) -> HandoffRef:
        async with self._database.session() as session:
            try:
                async with session.begin():
                    await self._validate_source(session, command)
                    replay = await session.scalar(
                        select(MemoryHandoffRecord).where(
                            MemoryHandoffRecord.operation_id == command.operation_id
                        )
                    )
                    if replay is not None:
                        return self._replayed(replay, command)
                    now = self._now()
                    active = await session.scalar(
                        select(MemoryHandoffRecord).where(
                            MemoryHandoffRecord.user_id == command.user_id,
                            MemoryHandoffRecord.status == "active",
                        )
                    )
                    if active is not None:
                        active.status = (
                            "expired"
                            if self._as_utc(active.expires_at) <= now
                            else "resolved"
                        )
                        active.resolved_at = now
                        await session.flush()
                    row = MemoryHandoffRecord(
                        user_id=command.user_id,
                        thread_id=command.thread_id,
                        status="active",
                        objective=command.objective,
                        unresolved_json=json.dumps(
                            command.unresolved,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        source_turn_id=command.source_turn_id,
                        source_item_id=command.source_item_id,
                        source_tool_call_id=command.source_tool_call_id,
                        operation_id=command.operation_id,
                        created_at=now,
                        expires_at=now + self._ttl,
                    )
                    session.add(row)
                    await session.flush()
                    return self._ref(row)
            except IntegrityError as exc:
                raise MemoryCollision(
                    "Handoff write conflicted with another operation"
                ) from exc

    async def active(self, user_id: str, *, at: datetime | None = None) -> HandoffRef | None:
        now = self._trusted_time(at) if at is not None else self._now()
        async with self._database.session() as session:
            row = await session.scalar(
                select(MemoryHandoffRecord).where(
                    MemoryHandoffRecord.user_id == user_id,
                    MemoryHandoffRecord.status == "active",
                    MemoryHandoffRecord.expires_at > now,
                )
            )
            return self._ref(row) if row is not None else None

    async def resolve(self, command: HandoffResolveCommand) -> tuple[HandoffRef, bool]:
        async with self._database.session() as session, session.begin():
            mismatch = await validate_record_source(
                session,
                user_id=command.user_id,
                source_turn_id=command.source_turn_id,
                source_item_id=command.source_item_id,
            )
            if mismatch is not None:
                raise MemorySourceMismatch(f"Handoff {mismatch}")
            source = await session.get(AgentItemRecord, command.source_item_id)
            if source is None or source.item_type != "user_message":
                raise MemorySourceMismatch(
                    "Handoff resolution requires a current user message"
                )
            row = await session.scalar(
                select(MemoryHandoffRecord).where(
                    MemoryHandoffRecord.id == command.handoff_id,
                    MemoryHandoffRecord.user_id == command.user_id,
                )
            )
            if row is None:
                raise MemoryNotFound("Handoff is not visible to the current user")
            if row.status != "active":
                return self._ref(row), False
            now = self._now()
            row.status = "expired" if self._as_utc(row.expires_at) <= now else "resolved"
            row.resolved_at = now
            await session.flush()
            return self._ref(row), True

    async def _validate_source(
        self,
        session: AsyncSession,
        command: HandoffUpsertCommand,
    ) -> None:
        mismatch = await validate_record_source(
            session,
            user_id=command.user_id,
            source_turn_id=command.source_turn_id,
            source_item_id=command.source_item_id,
        )
        if mismatch is not None:
            raise MemorySourceMismatch(f"Handoff {mismatch}")
        turn = await session.get(AgentTurnRecord, command.source_turn_id)
        if turn is None or turn.thread_id != command.thread_id:
            raise MemorySourceMismatch("Handoff Thread does not own its source Turn")
        source = await session.get(AgentItemRecord, command.source_item_id)
        if source is None or source.item_type != "user_message":
            raise MemoryEvidenceMismatch("Handoff writes require a current user message")
        try:
            payload = json.loads(source.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryEvidenceMismatch("Handoff source payload is invalid") from exc
        text = payload.get("text")
        if not isinstance(text, str) or command.evidence_excerpt not in text:
            raise MemoryEvidenceMismatch("Handoff evidence must come from the current message")

    @classmethod
    def _replayed(
        cls,
        row: MemoryHandoffRecord,
        command: HandoffUpsertCommand,
    ) -> HandoffRef:
        ref = cls._ref(row)
        if (
            ref.user_id != command.user_id
            or ref.thread_id != command.thread_id
            or ref.objective != command.objective
            or ref.unresolved != command.unresolved
            or row.source_turn_id != command.source_turn_id
            or row.source_item_id != command.source_item_id
            or row.source_tool_call_id != command.source_tool_call_id
        ):
            raise MemoryCollision("Handoff operation identity was reused with different data")
        return ref

    @classmethod
    def _ref(cls, row: MemoryHandoffRecord) -> HandoffRef:
        try:
            unresolved = json.loads(row.unresolved_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryCollision(f"Handoff payload is invalid: {row.id}") from exc
        if not isinstance(unresolved, list) or not all(
            isinstance(value, str) for value in unresolved
        ):
            raise MemoryCollision(f"Handoff payload is not a string list: {row.id}")
        return HandoffRef(
            id=row.id,
            user_id=row.user_id,
            thread_id=row.thread_id,
            status=row.status,
            objective=row.objective,
            unresolved=tuple(str(value) for value in unresolved),
            source_turn_id=row.source_turn_id,
            created_at=cls._as_utc(row.created_at),
            expires_at=cls._as_utc(row.expires_at),
            resolved_at=cls._as_utc(row.resolved_at) if row.resolved_at else None,
        )

    def _now(self) -> datetime:
        return self._trusted_time(self._clock())

    @staticmethod
    def _trusted_time(value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Handoff time must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
