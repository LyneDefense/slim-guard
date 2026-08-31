from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentThreadRecord, PendingActionRecord, utc_now
from slim_guard.db.session import Database
from slim_guard.harness.errors import (
    InvalidPendingActionTransition,
    PendingActionCollision,
    PendingActionStateConflict,
)
from slim_guard.harness.events import PendingActionStatus, PendingActionType
from slim_guard.tools.contracts import ToolExecutionMode

_RESOLUTIONS = frozenset(
    {
        PendingActionStatus.APPROVED,
        PendingActionStatus.REJECTED,
        PendingActionStatus.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class PendingActionRef:
    id: str
    thread_id: str
    turn_id: str
    source_item_id: str | None
    execution_key: str
    tool_call_id: str
    tool_name: str
    tool_version: str
    canonical_arguments: dict[str, Any]
    execution_mode: ToolExecutionMode
    isolated_write_environment: bool
    action_type: PendingActionType
    status: PendingActionStatus
    reason: str
    expires_at: datetime
    resolved_by: str | None
    created_at: datetime
    resolved_at: datetime | None
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PendingActionCreation:
    action: PendingActionRef
    created: bool


class PendingActionStore(Protocol):
    async def create(
        self,
        *,
        thread_id: str,
        turn_id: str,
        source_item_id: str | None,
        execution_key: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        canonical_arguments: Mapping[str, Any],
        execution_mode: ToolExecutionMode,
        isolated_write_environment: bool,
        action_type: PendingActionType,
        reason: str,
        expires_at: datetime,
    ) -> PendingActionCreation: ...

    async def get(self, action_id: str) -> PendingActionRef | None: ...

    async def list_for_execution(self, execution_key: str) -> list[PendingActionRef]: ...

    async def resolve(
        self,
        *,
        action_id: str,
        resolution: PendingActionStatus,
        resolved_by: str,
        resolved_at: datetime | None = None,
    ) -> PendingActionRef: ...

    async def consume(
        self,
        *,
        action_id: str,
        consumed_at: datetime | None = None,
    ) -> PendingActionRef: ...


class PendingActionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(
        self,
        *,
        thread_id: str,
        turn_id: str,
        source_item_id: str | None,
        execution_key: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        canonical_arguments: Mapping[str, Any],
        execution_mode: ToolExecutionMode,
        isolated_write_environment: bool,
        action_type: PendingActionType,
        reason: str,
        expires_at: datetime,
    ) -> PendingActionCreation:
        arguments_json = self._canonical_json(canonical_arguments)
        normalized_expiry = self._as_utc(expires_at)
        row = PendingActionRecord(
            thread_id=thread_id,
            turn_id=turn_id,
            source_item_id=source_item_id,
            execution_key=execution_key,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_version=tool_version,
            canonical_arguments_json=arguments_json,
            execution_mode=execution_mode.value,
            isolated_write_environment=isolated_write_environment,
            action_type=action_type.value,
            status=PendingActionStatus.PENDING.value,
            reason=reason,
            expires_at=normalized_expiry,
        )
        async with self.database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return PendingActionCreation(action=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(PendingActionRecord).where(
                        PendingActionRecord.execution_key == execution_key,
                        PendingActionRecord.action_type == action_type.value,
                    )
                )
                if existing is None:
                    raise PendingActionCollision(
                        "Pending action conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_creation(
                    existing,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    source_item_id=source_item_id,
                    execution_key=execution_key,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    arguments_json=arguments_json,
                    execution_mode=execution_mode,
                    isolated_write_environment=isolated_write_environment,
                    action_type=action_type,
                    reason=reason,
                    expires_at=normalized_expiry,
                )
                return PendingActionCreation(action=self._ref(existing), created=False)

    async def get(self, action_id: str) -> PendingActionRef | None:
        async with self.database.session() as session:
            row = await session.get(PendingActionRecord, action_id)
            return self._ref(row) if row is not None else None

    async def list_for_execution(self, execution_key: str) -> list[PendingActionRef]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(PendingActionRecord)
                .where(PendingActionRecord.execution_key == execution_key)
                .order_by(PendingActionRecord.created_at)
            )
            return [self._ref(row) for row in rows]

    async def list_open(
        self,
        *,
        thread_id: str,
        at: datetime,
    ) -> list[PendingActionRef]:
        normalized_at = self._as_utc(at)
        async with self.database.session() as session:
            rows = await session.scalars(
                select(PendingActionRecord)
                .where(
                    PendingActionRecord.thread_id == thread_id,
                    PendingActionRecord.status == PendingActionStatus.PENDING.value,
                    PendingActionRecord.expires_at > normalized_at,
                )
                .order_by(PendingActionRecord.created_at)
            )
            return [self._ref(row) for row in rows]

    async def list_open_for_user(
        self,
        *,
        user_id: str,
        at: datetime,
    ) -> list[PendingActionRef]:
        normalized_at = self._as_utc(at)
        async with self.database.session() as session:
            rows = await session.scalars(
                select(PendingActionRecord)
                .join(
                    AgentThreadRecord,
                    AgentThreadRecord.id == PendingActionRecord.thread_id,
                )
                .where(
                    AgentThreadRecord.user_id == user_id,
                    PendingActionRecord.status == PendingActionStatus.PENDING.value,
                    PendingActionRecord.action_type == PendingActionType.USER_CONFIRMATION.value,
                    PendingActionRecord.expires_at > normalized_at,
                )
                .order_by(PendingActionRecord.created_at, PendingActionRecord.id)
            )
            return [self._ref(row) for row in rows]

    async def resolve(
        self,
        *,
        action_id: str,
        resolution: PendingActionStatus,
        resolved_by: str,
        resolved_at: datetime | None = None,
    ) -> PendingActionRef:
        if resolution not in _RESOLUTIONS:
            raise InvalidPendingActionTransition(
                f"Pending actions cannot be resolved as {resolution.value}"
            )
        now = self._as_utc(resolved_at or utc_now())
        async with self.database.session() as session, session.begin():
            row = await session.get(PendingActionRecord, action_id)
            if row is None:
                raise LookupError(f"Pending action not found: {action_id}")
            current = PendingActionStatus(row.status)
            if current is resolution:
                if row.resolved_by != resolved_by:
                    raise PendingActionStateConflict(
                        f"Pending action {action_id} was resolved by another actor"
                    )
                return self._ref(row)
            if (
                current is PendingActionStatus.CONSUMED
                and resolution is PendingActionStatus.APPROVED
                and row.resolved_by == resolved_by
            ):
                return self._ref(row)
            if current is not PendingActionStatus.PENDING:
                raise InvalidPendingActionTransition(
                    f"Cannot resolve pending action {action_id} from {current.value}"
                )
            if self._as_utc(row.expires_at) <= now:
                return await self._transition(
                    session=session,
                    row=row,
                    expected=PendingActionStatus.PENDING,
                    target=PendingActionStatus.EXPIRED,
                    changed_at=now,
                    resolved_by=None,
                )
            return await self._transition(
                session=session,
                row=row,
                expected=PendingActionStatus.PENDING,
                target=resolution,
                changed_at=now,
                resolved_by=resolved_by,
            )

    async def consume(
        self,
        *,
        action_id: str,
        consumed_at: datetime | None = None,
    ) -> PendingActionRef:
        now = self._as_utc(consumed_at or utc_now())
        async with self.database.session() as session, session.begin():
            row = await session.get(PendingActionRecord, action_id)
            if row is None:
                raise LookupError(f"Pending action not found: {action_id}")
            current = PendingActionStatus(row.status)
            if current is PendingActionStatus.CONSUMED:
                return self._ref(row)
            if current is not PendingActionStatus.APPROVED:
                raise InvalidPendingActionTransition(
                    f"Cannot consume pending action {action_id} from {current.value}"
                )
            return await self._transition(
                session=session,
                row=row,
                expected=PendingActionStatus.APPROVED,
                target=PendingActionStatus.CONSUMED,
                changed_at=now,
                resolved_by=row.resolved_by,
            )

    async def expire_due(self, *, at: datetime) -> int:
        normalized_at = self._as_utc(at)
        async with self.database.session() as session, session.begin():
            result = await session.execute(
                update(PendingActionRecord)
                .where(
                    PendingActionRecord.status == PendingActionStatus.PENDING.value,
                    PendingActionRecord.expires_at <= normalized_at,
                )
                .values(
                    status=PendingActionStatus.EXPIRED.value,
                    updated_at=normalized_at,
                    resolved_at=normalized_at,
                )
            )
            return cast(CursorResult[Any], result).rowcount

    async def _transition(
        self,
        *,
        session: AsyncSession,
        row: PendingActionRecord,
        expected: PendingActionStatus,
        target: PendingActionStatus,
        changed_at: datetime,
        resolved_by: str | None,
    ) -> PendingActionRef:
        values: dict[str, Any] = {
            "status": target.value,
            "updated_at": changed_at,
        }
        if target is PendingActionStatus.CONSUMED:
            values["consumed_at"] = changed_at
        else:
            values["resolved_at"] = changed_at
            values["resolved_by"] = resolved_by
        result = await session.execute(
            update(PendingActionRecord)
            .where(
                PendingActionRecord.id == row.id,
                PendingActionRecord.status == expected.value,
            )
            .values(**values)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            await session.refresh(row)
            if PendingActionStatus(row.status) is target and (
                target is PendingActionStatus.CONSUMED or row.resolved_by == resolved_by
            ):
                return self._ref(row)
            raise PendingActionStateConflict(f"Pending action {row.id} changed concurrently")
        await session.refresh(row)
        return self._ref(row)

    @staticmethod
    def _assert_same_creation(
        row: PendingActionRecord,
        *,
        thread_id: str,
        turn_id: str,
        source_item_id: str | None,
        execution_key: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        arguments_json: str,
        execution_mode: ToolExecutionMode,
        isolated_write_environment: bool,
        action_type: PendingActionType,
        reason: str,
        expires_at: datetime,
    ) -> None:
        expected = (
            thread_id,
            turn_id,
            source_item_id,
            execution_key,
            tool_call_id,
            tool_name,
            tool_version,
            arguments_json,
            execution_mode.value,
            isolated_write_environment,
            action_type.value,
            reason,
            expires_at,
        )
        actual = (
            row.thread_id,
            row.turn_id,
            row.source_item_id,
            row.execution_key,
            row.tool_call_id,
            row.tool_name,
            row.tool_version,
            row.canonical_arguments_json,
            row.execution_mode,
            row.isolated_write_environment,
            row.action_type,
            row.reason,
            PendingActionRepository._as_utc(row.expires_at),
        )
        same_identity = actual[:7] == expected[:7]
        same_arguments = actual[7] == expected[7] or (
            PendingActionRepository._matches_redacted_json(
                row.canonical_arguments_json,
                arguments_json,
            )
        )
        same_tail = actual[8:] == expected[8:]
        if not same_identity or not same_arguments or not same_tail:
            raise PendingActionCollision(
                f"Pending action identity collision for execution {execution_key}"
            )

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _ref(row: PendingActionRecord) -> PendingActionRef:
        arguments = json.loads(row.canonical_arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError(f"Pending action arguments are not an object: {row.id}")
        return PendingActionRef(
            id=row.id,
            thread_id=row.thread_id,
            turn_id=row.turn_id,
            source_item_id=row.source_item_id,
            execution_key=row.execution_key,
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            canonical_arguments=arguments,
            execution_mode=ToolExecutionMode(row.execution_mode),
            isolated_write_environment=row.isolated_write_environment,
            action_type=PendingActionType(row.action_type),
            status=PendingActionStatus(row.status),
            reason=row.reason,
            expires_at=PendingActionRepository._as_utc(row.expires_at),
            resolved_by=row.resolved_by,
            created_at=PendingActionRepository._as_utc(row.created_at),
            resolved_at=(
                PendingActionRepository._as_utc(row.resolved_at)
                if row.resolved_at is not None
                else None
            ),
            consumed_at=(
                PendingActionRepository._as_utc(row.consumed_at)
                if row.consumed_at is not None
                else None
            ),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _matches_redacted_json(stored_json: str, expected_json: str) -> bool:
        try:
            payload = json.loads(stored_json)
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("_redacted") is True
            and payload.get("_redacted_sha256")
            == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        )
