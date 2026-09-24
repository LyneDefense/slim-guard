from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.tool_operations.contracts import OperationClaim, OperationStatus, ToolOperation
from slim_guard.tool_operations.models import ToolOperationRecord
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResult


def _scope(context: ToolContext, tool_name: str) -> str:
    value = [context.user_id, context.thread_id, context.execution_mode.value, tool_name]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


class ToolOperationRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self._clock = clock or utc_now

    async def reconcile_success(self, operation: ToolOperation, result: ToolResult) -> bool:
        async with self.database.session() as session, session.begin():
            updated = await session.execute(
                update(ToolOperationRecord)
                .where(
                    ToolOperationRecord.id == operation.id,
                    ToolOperationRecord.attempt == operation.attempt,
                    ToolOperationRecord.status.in_(("running", "unknown")),
                )
                .values(
                    status="succeeded",
                    result_json=result.to_model_content(),
                    unsettled_scope=None,
                    updated_at=utc_now(),
                )
            )
            return cast(CursorResult[Any], updated).rowcount == 1

    async def start(
        self,
        *,
        tool_name: str,
        tool_version: str,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> OperationClaim:
        if context.execution_idempotency_key is None:
            raise ValueError("An operation requires a claimed tool attempt")
        operation_id = f"op-{uuid4()}"
        frozen = context.model_copy(
            update={
                "execution_idempotency_key": operation_id,
                "operation_started_at": self._clock(),
            }
        )
        scope = _scope(context, tool_name)
        row = ToolOperationRecord(
            id=operation_id,
            initial_execution_key=context.execution_idempotency_key,
            user_id=context.user_id,
            thread_id=context.thread_id,
            source_turn_id=context.turn_id,
            tool_name=tool_name,
            tool_version=tool_version,
            context_json=frozen.model_dump_json(),
            arguments_json=json.dumps(arguments),
            status="running",
            unsettled_scope=scope,
            attempt=1,
        )
        async with self.database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return OperationClaim(operation=self._ref(row), acquired=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(ToolOperationRecord).where(
                        ToolOperationRecord.initial_execution_key
                        == context.execution_idempotency_key,
                    )
                )
                if existing is None:
                    existing = await session.scalar(
                        select(ToolOperationRecord).where(
                            ToolOperationRecord.unsettled_scope == scope,
                        )
                    )
                if existing is None:
                    raise
                return OperationClaim(operation=self._ref(existing), acquired=False)

    async def get(self, operation_id: str, *, context: ToolContext) -> ToolOperation | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(ToolOperationRecord).where(
                    ToolOperationRecord.id == operation_id,
                    ToolOperationRecord.user_id == context.user_id,
                    ToolOperationRecord.thread_id == context.thread_id,
                )
            )
            if row is None:
                return None
            operation = self._ref(row)
            if operation.context.execution_mode != context.execution_mode:
                return None
            return operation

    async def list_unsettled(
        self,
        *,
        user_id: str,
        thread_id: str,
        execution_mode: ToolExecutionMode,
    ) -> tuple[ToolOperation, ...]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(ToolOperationRecord)
                .where(
                    ToolOperationRecord.user_id == user_id,
                    ToolOperationRecord.thread_id == thread_id,
                    ToolOperationRecord.status != "succeeded",
                )
                .order_by(ToolOperationRecord.created_at.desc())
                .limit(100)
            )
            return tuple(
                operation
                for row in rows
                if (operation := self._ref(row)).context.execution_mode == execution_mode
            )

    async def acquire_retry(self, operation: ToolOperation) -> bool:
        async with self.database.session() as session:
            try:
                result = await session.execute(
                    update(ToolOperationRecord)
                    .where(
                        ToolOperationRecord.id == operation.id,
                        ToolOperationRecord.status == "not_applied",
                        ToolOperationRecord.attempt == operation.attempt,
                    )
                    .values(
                        status="running",
                        result_json=None,
                        attempt=operation.attempt + 1,
                        unsettled_scope=_scope(operation.context, operation.tool_name),
                        updated_at=utc_now(),
                    )
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return cast(CursorResult[Any], result).rowcount == 1

    async def finish(
        self,
        operation: ToolOperation,
        *,
        status: OperationStatus,
        result: ToolResult,
    ) -> None:
        async with self.database.session() as session, session.begin():
            result_update = await session.execute(
                update(ToolOperationRecord)
                .where(
                    ToolOperationRecord.id == operation.id,
                    ToolOperationRecord.status == "running",
                    ToolOperationRecord.attempt == operation.attempt,
                )
                .values(
                    status=status,
                    result_json=result.to_model_content(),
                    updated_at=utc_now(),
                    unsettled_scope=(
                        _scope(operation.context, operation.tool_name)
                        if status == "unknown"
                        else None
                    ),
                )
            )
            if cast(CursorResult[Any], result_update).rowcount != 1:
                # An authoritative read may have already observed the committed
                # business row while its original handler was returning.
                existing = await session.get(ToolOperationRecord, operation.id)
                if (
                    existing is not None
                    and existing.status == "succeeded"
                    and existing.attempt == operation.attempt
                ):
                    return
                raise RuntimeError("Operation attempt no longer owns completion")

    @staticmethod
    def _ref(row: ToolOperationRecord) -> ToolOperation:
        return ToolOperation(
            id=row.id,
            initial_execution_key=row.initial_execution_key,
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            context=ToolContext.model_validate_json(row.context_json),
            arguments=json.loads(row.arguments_json),
            status=cast(OperationStatus, row.status),
            attempt=row.attempt,
            result=ToolResult.model_validate_json(row.result_json) if row.result_json else None,
        )
