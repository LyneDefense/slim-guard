from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
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
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.exercise import (
    ExerciseToolHandlers,
    GetRecentExerciseArguments,
    RecordExerciseArguments,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


async def prepare(
    tmp_path: Path,
) -> tuple[Database, ExerciseRepository, ToolContext]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'exercise-tools.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
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
            inputs=(TurnInput.user_message(text="走了8300步，大约5.7公里"),),
        )
    )
    return (
        database,
        ExerciseRepository(database),
        ToolContext(
            thread_id=initialized.thread.id,
            turn_id=initialized.turn.id,
            tool_call_id="call-exercise",
            user_id="user-1",
            agent_version_id=manifest.version_id,
            execution_mode=ToolExecutionMode.EVALUATION,
            source_item_id=initialized.source_item_id,
            execution_idempotency_key="tool-exercise-1",
        ),
    )


async def test_record_and_read_exercise_tools_with_distance_conversion(
    tmp_path: Path,
) -> None:
    database, repository, context = await prepare(tmp_path)
    handlers = ExerciseToolHandlers(repository, clock=lambda: NOW)
    try:
        recorded = await handlers.record_exercise(
            context,
            RecordExerciseArguments(
                activity_name="步行",
                duration_minutes=45,
                steps=8300,
                distance_value=5.7,
                distance_unit="km",
            ),
        )
        recent = await handlers.get_recent_exercise(
            context.model_copy(update={"tool_call_id": "call-recent-exercise"}),
            GetRecentExerciseArguments(limit=10),
        )

        assert recorded.status is ToolResultStatus.SUCCEEDED
        assert recorded.output["distance_meters"] == 5700
        assert recorded.output["steps"] == 8300
        assert recent.status is ToolResultStatus.SUCCEEDED
        assert recent.output["records"][0]["activity_name"] == "步行"
        assert recent.source_ids == recorded.source_ids
    finally:
        await database.close()


async def test_record_exercise_rejects_out_of_range_converted_distance(
    tmp_path: Path,
) -> None:
    database, repository, context = await prepare(tmp_path)
    handlers = ExerciseToolHandlers(repository, clock=lambda: NOW)
    try:
        result = await handlers.record_exercise(
            context,
            RecordExerciseArguments(
                activity_name="跑步",
                distance_value=2000,
                distance_unit="km",
            ),
        )

        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "invalid_exercise_record"
    finally:
        await database.close()
