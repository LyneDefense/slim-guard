from __future__ import annotations

from datetime import UTC, datetime

from slim_guard.db.models import SlimGuardUser, WeightRecord
from slim_guard.db.session import Database
from slim_guard.domain.records.service import UserRecordStatusService
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.records import (
    RecordStatusToolHandlers,
    UpdateRecordStatusArguments,
)

NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)


async def prepare(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'record-status.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
        session.add(
            WeightRecord(
                id="weight-1",
                user_id="user-1",
                weight_grams=77_600,
                original_value="77.6",
                original_unit="kg",
                measured_at=NOW,
                measurement_condition="fasting",
                status="active",
                idempotency_key="weight-key-1",
                source_turn_id="turn-1",
                source_tool_call_id="call-record",
            )
        )
    return database


def context(user_id: str) -> ToolContext:
    return ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-status",
        user_id=user_id,
        agent_version_id="agent-1",
        execution_mode=ToolExecutionMode.EVALUATION,
        execution_idempotency_key="status-key-1",
    )


async def test_user_can_void_restore_and_repeat_own_record(tmp_path) -> None:
    database = await prepare(tmp_path)
    handlers = RecordStatusToolHandlers(UserRecordStatusService(database))
    try:
        voided = await handlers.update(
            context("user-1"),
            UpdateRecordStatusArguments(
                record_kind="weight",
                record_id="weight-1",
                action="void",
            ),
        )
        repeated = await handlers.update(
            context("user-1"),
            UpdateRecordStatusArguments(
                record_kind="weight",
                record_id="weight-1",
                action="void",
            ),
        )
        restored = await handlers.update(
            context("user-1"),
            UpdateRecordStatusArguments(
                record_kind="weight",
                record_id="weight-1",
                action="restore",
            ),
        )

        assert voided.status is ToolResultStatus.SUCCEEDED
        assert voided.output["status"] == "voided"
        assert voided.output["changed"] is True
        assert repeated.output["changed"] is False
        assert restored.output["status"] == "active"
    finally:
        await database.close()


async def test_user_cannot_change_another_users_record(tmp_path) -> None:
    database = await prepare(tmp_path)
    handlers = RecordStatusToolHandlers(UserRecordStatusService(database))
    try:
        result = await handlers.update(
            context("user-2"),
            UpdateRecordStatusArguments(
                record_kind="weight",
                record_id="weight-1",
                action="void",
            ),
        )

        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "record_not_found"
    finally:
        await database.close()
