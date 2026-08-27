from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import ToolExecutionRecord, utc_now
from slim_guard.db.session import Database
from slim_guard.tools.contracts import (
    ToolExecutionStatus,
    ToolResult,
    ToolResultStatus,
)
from slim_guard.tools.errors import ToolExecutionCollision, ToolExecutionStateConflict


@dataclass(frozen=True, slots=True)
class ToolExecutionRef:
    idempotency_key: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    tool_version: str
    canonical_arguments: dict[str, Any]
    status: ToolExecutionStatus
    result: ToolResult | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToolExecutionClaim:
    execution: ToolExecutionRef
    created: bool


class ToolExecutionRepository:
    """Persistent claim-and-complete ledger for idempotent tool execution."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def claim(
        self,
        *,
        idempotency_key: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        canonical_arguments: Mapping[str, Any],
    ) -> ToolExecutionClaim:
        arguments_json = self._canonical_json(canonical_arguments)
        row = ToolExecutionRecord(
            idempotency_key=idempotency_key,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_version=tool_version,
            canonical_arguments_json=arguments_json,
            status=ToolExecutionStatus.RUNNING.value,
        )
        async with self.database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return ToolExecutionClaim(execution=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.get(ToolExecutionRecord, idempotency_key)
                if existing is None:
                    existing = await session.scalar(
                        select(ToolExecutionRecord).where(
                            ToolExecutionRecord.turn_id == turn_id,
                            ToolExecutionRecord.tool_call_id == tool_call_id,
                        )
                    )
                if existing is None:
                    raise ToolExecutionCollision(
                        "Tool execution conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_claim(
                    existing,
                    idempotency_key=idempotency_key,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    arguments_json=arguments_json,
                )
                return ToolExecutionClaim(execution=self._ref(existing), created=False)

    async def get(self, idempotency_key: str) -> ToolExecutionRef | None:
        async with self.database.session() as session:
            row = await session.get(ToolExecutionRecord, idempotency_key)
            return self._ref(row) if row is not None else None

    async def complete(
        self,
        *,
        idempotency_key: str,
        result: ToolResult,
    ) -> ToolExecutionRef:
        target = self._status_for_result(result)
        result_json = result.to_model_content()
        completed_at = utc_now()
        async with self.database.session() as session, session.begin():
            row = await session.get(ToolExecutionRecord, idempotency_key)
            if row is None:
                raise LookupError(f"Tool execution not found: {idempotency_key}")
            current = ToolExecutionStatus(row.status)
            if current is not ToolExecutionStatus.RUNNING:
                self._assert_same_completion(row, target=target, result_json=result_json)
                return self._ref(row)

            update_result = await session.execute(
                update(ToolExecutionRecord)
                .where(
                    ToolExecutionRecord.idempotency_key == idempotency_key,
                    ToolExecutionRecord.status == ToolExecutionStatus.RUNNING.value,
                )
                .values(
                    status=target.value,
                    result_json=result_json,
                    updated_at=completed_at,
                    completed_at=completed_at,
                )
            )
            if cast(CursorResult[Any], update_result).rowcount != 1:
                await session.refresh(row)
                self._assert_same_completion(row, target=target, result_json=result_json)
                return self._ref(row)
            await session.refresh(row)
            return self._ref(row)

    @staticmethod
    def _status_for_result(result: ToolResult) -> ToolExecutionStatus:
        if result.status is ToolResultStatus.SUCCEEDED:
            return ToolExecutionStatus.SUCCEEDED
        return ToolExecutionStatus.FAILED

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _assert_same_claim(
        row: ToolExecutionRecord,
        *,
        idempotency_key: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        arguments_json: str,
    ) -> None:
        expected = (
            idempotency_key,
            turn_id,
            tool_call_id,
            tool_name,
            tool_version,
            arguments_json,
        )
        actual = (
            row.idempotency_key,
            row.turn_id,
            row.tool_call_id,
            row.tool_name,
            row.tool_version,
            row.canonical_arguments_json,
        )
        if actual != expected:
            raise ToolExecutionCollision(
                f"Tool execution identity collision for turn {turn_id}, call {tool_call_id}"
            )

    @staticmethod
    def _assert_same_completion(
        row: ToolExecutionRecord,
        *,
        target: ToolExecutionStatus,
        result_json: str,
    ) -> None:
        if row.status != target.value or row.result_json != result_json:
            raise ToolExecutionStateConflict(
                f"Tool execution {row.idempotency_key} already completed differently"
            )

    @staticmethod
    def _ref(row: ToolExecutionRecord) -> ToolExecutionRef:
        arguments = json.loads(row.canonical_arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError(
                f"Tool execution arguments are not an object: {row.idempotency_key}"
            )
        result = (
            ToolResult.model_validate_json(row.result_json)
            if row.result_json is not None
            else None
        )
        return ToolExecutionRef(
            idempotency_key=row.idempotency_key,
            turn_id=row.turn_id,
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            canonical_arguments=arguments,
            status=ToolExecutionStatus(row.status),
            result=result,
            created_at=ToolExecutionRepository._as_utc(row.created_at),
            completed_at=(
                ToolExecutionRepository._as_utc(row.completed_at)
                if row.completed_at is not None
                else None
            ),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
