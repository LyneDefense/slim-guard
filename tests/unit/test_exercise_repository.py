from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.exercise.contracts import ExerciseRecordCommand
from slim_guard.domain.exercise.errors import (
    ExerciseRecordCollision,
    ExerciseSourceMismatch,
)
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


async def prepare_repository(
    tmp_path: Path,
) -> tuple[Database, ExerciseRepository, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'exercise.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    manifest = AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={},
        system_prompt_version="test-v1",
        system_prompt="test",
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test",
    )
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="走了8300步"),),
        )
    )
    assert initialized.source_item_id is not None
    return (
        database,
        ExerciseRepository(database),
        initialized.turn.id,
        initialized.source_item_id,
    )


def command(
    *,
    turn_id: str,
    item_id: str,
    user_id: str = "user-1",
    idempotency_key: str = "exercise-execution-1",
    occurred_at: datetime = NOW,
) -> ExerciseRecordCommand:
    return ExerciseRecordCommand(
        user_id=user_id,
        activity_name="步行",
        duration_minutes=45,
        steps=8300,
        distance_meters=5700,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        source_turn_id=turn_id,
        source_item_id=item_id,
        source_tool_call_id="call-exercise-1",
    )


async def test_exercise_record_is_idempotent_and_newest_first(tmp_path: Path) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        first = await repository.record(command(turn_id=turn_id, item_id=item_id))
        repeated = await repository.record(command(turn_id=turn_id, item_id=item_id))
        later = await repository.record(
            command(
                turn_id=turn_id,
                item_id=item_id,
                idempotency_key="exercise-execution-2",
                occurred_at=NOW + timedelta(days=1),
            )
        )
        recent = await repository.recent("user-1")

        assert first.created is True
        assert repeated.created is False
        assert repeated.record == first.record
        assert [record.id for record in recent] == [later.record.id, first.record.id]
        assert first.record.steps == 8300
    finally:
        await database.close()


async def test_exercise_source_must_belong_to_owner(tmp_path: Path) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        with pytest.raises(ExerciseSourceMismatch, match="another user"):
            await repository.record(
                command(turn_id=turn_id, item_id=item_id, user_id="user-2")
            )
    finally:
        await database.close()


async def test_exercise_idempotency_collision_cannot_change_steps(tmp_path: Path) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        original = command(turn_id=turn_id, item_id=item_id)
        await repository.record(original)

        with pytest.raises(ExerciseRecordCollision, match="idempotency collision"):
            await repository.record(original.model_copy(update={"steps": 9000}))
    finally:
        await database.close()
