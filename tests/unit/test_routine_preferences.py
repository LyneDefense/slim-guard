from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.routine.contracts import (
    RoutinePreferenceCommand,
    RoutineSetting,
)
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.routine import ConfigureRoutineArguments, RoutineToolHandlers

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


async def prepare(tmp_path) -> tuple[Database, RoutinePreferenceRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'routine.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    return database, RoutinePreferenceRepository(database)


def test_routine_contract_rejects_invalid_timezone_and_enabled_without_time() -> None:
    with pytest.raises(ValidationError, match="Unknown IANA timezone"):
        RoutinePreferenceCommand(user_id="user-1", timezone="Mars/Olympus")
    with pytest.raises(ValidationError, match="requires a local time"):
        RoutinePreferenceCommand(
            user_id="user-1",
            weight=RoutineSetting(enabled=True),
        )


async def test_repository_partially_updates_and_disables_routines(tmp_path) -> None:
    database, repository = await prepare(tmp_path)
    try:
        created = await repository.update(
            RoutinePreferenceCommand(
                user_id="user-1",
                timezone="Asia/Shanghai",
                weight=RoutineSetting(enabled=True, local_time="08:00"),
                daily_review=RoutineSetting(enabled=True, local_time="21:30"),
            )
        )
        updated = await repository.update(
            RoutinePreferenceCommand(
                user_id="user-1",
                weight=RoutineSetting(enabled=False),
                meal=RoutineSetting(enabled=True, local_time="19:00"),
            )
        )

        assert created.weight_reminder_time == "08:00"
        assert updated.timezone == "Asia/Shanghai"
        assert updated.weight_reminder_time is None
        assert updated.meal_reminder_time == "19:00"
        assert updated.daily_review_time == "21:30"
        assert await repository.list_enabled() == (updated,)
    finally:
        await database.close()


async def test_routine_tool_is_scoped_to_current_harness_user(tmp_path) -> None:
    database, repository = await prepare(tmp_path)
    context = ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        user_id="user-1",
        agent_version_id="agent-1",
        execution_mode=ToolExecutionMode.EVALUATION,
    )
    handlers = RoutineToolHandlers(repository)
    try:
        result = await handlers.configure(
            context,
            ConfigureRoutineArguments(
                timezone="Asia/Shanghai",
                weight=RoutineSetting(enabled=True, local_time="07:45"),
            ),
        )

        assert result.status is ToolResultStatus.SUCCEEDED
        assert result.output["weight_reminder_time"] == "07:45"
        stored = await repository.get("user-1")
        assert stored is not None
        assert stored.user_id == context.user_id
    finally:
        await database.close()
