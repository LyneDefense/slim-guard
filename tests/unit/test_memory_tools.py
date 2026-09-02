from __future__ import annotations

from datetime import UTC, datetime

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.memory import (
    ClearUserMemoriesArguments,
    ForgetUserMemoryArguments,
    ListUserMemoriesArguments,
    MemoryToolHandlers,
    RecordUserConstraintArguments,
    SetBehaviorGoalArguments,
    SetBodyFatGoalArguments,
    SetBodyProfileArguments,
    SetCoachingProfileArguments,
    SetExerciseProfileArguments,
    SetWeightGoalArguments,
    UpsertExercisePreferenceArguments,
    UpsertFoodPreferenceArguments,
)

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


async def test_memory_tools_bind_writes_and_reads_to_current_harness_user(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-tools.sqlite3'}")
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
        memory_policy_version="profile-goal-constraint-v2",
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
            inputs=(
                TurnInput.user_message(
                    text=(
                        "以后叫我阿杰，身高179，我不喜欢香菜，也喜欢游泳，目标65，"
                        "体脂目标24%，目前不运动，"
                        "每周运动3次，我对花生过敏"
                        "，清空我的个性化记忆"
                    )
                ),
            ),
        )
    )
    assert initialized.source_item_id is not None
    context = ToolContext(
        thread_id=initialized.thread.id,
        turn_id=initialized.turn.id,
        tool_call_id="call-profile",
        user_id="user-1",
        agent_version_id=manifest.version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
        source_item_id=initialized.source_item_id,
        execution_idempotency_key="execution-profile",
    )
    repository = MemoryRepository(database)
    handlers = MemoryToolHandlers(repository)
    try:
        profile = await handlers.set_coaching_profile(
            context,
            SetCoachingProfileArguments(
                preferred_name="阿杰",
                response_style="concise",
                evidence_excerpt="以后叫我阿杰",
            ),
        )
        body_profile = await handlers.set_body_profile(
            context.model_copy(
                update={
                    "tool_call_id": "call-body-profile",
                    "execution_idempotency_key": "execution-body-profile",
                }
            ),
            SetBodyProfileArguments(
                height_value=179,
                evidence_excerpt="身高179",
            ),
        )
        exercise_profile = await handlers.set_exercise_profile(
            context.model_copy(
                update={
                    "tool_call_id": "call-exercise-profile",
                    "execution_idempotency_key": "execution-exercise-profile",
                }
            ),
            SetExerciseProfileArguments(
                habit_summary="目前不运动",
                evidence_excerpt="目前不运动",
            ),
        )
        mismatched_height_unit = await handlers.set_body_profile(
            context.model_copy(
                update={
                    "tool_call_id": "call-body-profile-wrong-unit",
                    "execution_idempotency_key": "execution-body-profile-wrong-unit",
                }
            ),
            SetBodyProfileArguments(
                height_value=1.79,
                height_unit="m",
                evidence_excerpt="身高1.79cm",
            ),
        )
        food = await handlers.upsert_food_preference(
            context.model_copy(
                update={
                    "tool_call_id": "call-food",
                    "execution_idempotency_key": "execution-food",
                }
            ),
            UpsertFoodPreferenceArguments(
                item="香菜",
                stance="dislike",
                evidence_excerpt="不喜欢香菜",
            ),
        )
        exercise = await handlers.upsert_exercise_preference(
            context.model_copy(
                update={
                    "tool_call_id": "call-exercise",
                    "execution_idempotency_key": "execution-exercise",
                }
            ),
            UpsertExercisePreferenceArguments(
                activity="游泳",
                stance="like",
                evidence_excerpt="喜欢游泳",
            ),
        )
        weight_goal = await handlers.set_weight_goal(
            context.model_copy(
                update={
                    "tool_call_id": "call-weight-goal",
                    "execution_idempotency_key": "execution-weight-goal",
                }
            ),
            SetWeightGoalArguments(
                value=65.0,
                evidence_excerpt="目标65",
            ),
        )
        mismatched_weight_unit = await handlers.set_weight_goal(
            context.model_copy(
                update={
                    "tool_call_id": "call-weight-goal-wrong-unit",
                    "execution_idempotency_key": "execution-weight-goal-wrong-unit",
                }
            ),
            SetWeightGoalArguments(
                value=65,
                unit="jin",
                evidence_excerpt="目标65公斤",
            ),
        )
        body_fat_goal = await handlers.set_body_fat_goal(
            context.model_copy(
                update={
                    "tool_call_id": "call-body-fat-goal",
                    "execution_idempotency_key": "execution-body-fat-goal",
                }
            ),
            SetBodyFatGoalArguments(
                value=24,
                evidence_excerpt="体脂目标24%",
            ),
        )
        behavior_goal = await handlers.set_behavior_goal(
            context.model_copy(
                update={
                    "tool_call_id": "call-behavior-goal",
                    "execution_idempotency_key": "execution-behavior-goal",
                }
            ),
            SetBehaviorGoalArguments(
                kind="weekly_exercise_sessions",
                target=3,
                evidence_excerpt="每周运动3次",
            ),
        )
        constraint = await handlers.record_user_constraint(
            context.model_copy(
                update={
                    "tool_call_id": "call-constraint",
                    "execution_idempotency_key": "execution-constraint",
                }
            ),
            RecordUserConstraintArguments(
                category="dietary",
                subject="花生",
                statement="我对花生过敏",
                evidence_excerpt="我对花生过敏",
            ),
        )
        listed = await handlers.list_user_memories(
            context.model_copy(update={"tool_call_id": "call-list"}),
            ListUserMemoriesArguments(),
        )

        assert profile.status is ToolResultStatus.SUCCEEDED
        assert profile.output["created_count"] == 2
        assert body_profile.status is ToolResultStatus.SUCCEEDED
        assert mismatched_height_unit.status is ToolResultStatus.FAILED
        assert mismatched_height_unit.failure is not None
        assert mismatched_height_unit.failure.code == "memory_value_not_in_evidence"
        assert exercise_profile.status is ToolResultStatus.SUCCEEDED
        assert food.status is ToolResultStatus.SUCCEEDED
        assert exercise.status is ToolResultStatus.SUCCEEDED
        assert weight_goal.status is ToolResultStatus.SUCCEEDED
        assert mismatched_weight_unit.status is ToolResultStatus.FAILED
        assert body_fat_goal.status is ToolResultStatus.SUCCEEDED
        assert behavior_goal.status is ToolResultStatus.SUCCEEDED
        assert constraint.status is ToolResultStatus.SUCCEEDED
        assert len(listed.output["memories"]) == 10
        assert {item["key"] for item in listed.output["memories"]} == {
            "identity.preferred_name",
            "profile.height",
            "profile.exercise_habit",
            "coaching.response_style",
            "food.preference",
            "exercise.preference",
            "goal.target_weight",
            "goal.target_body_fat",
            "goal.behavior",
            "constraint.dietary",
        }
        food_memory = next(
            memory
            for memory in listed.output["memories"]
            if memory["key"] == "food.preference"
        )
        forgotten = await handlers.forget_user_memory(
            context.model_copy(
                update={
                    "tool_call_id": "call-forget",
                    "execution_idempotency_key": "execution-forget",
                }
            ),
            ForgetUserMemoryArguments(memory_id=food_memory["memory_id"]),
        )
        remaining = await handlers.list_user_memories(
            context.model_copy(update={"tool_call_id": "call-list-after"}),
            ListUserMemoriesArguments(),
        )
        assert forgotten.status is ToolResultStatus.SUCCEEDED
        assert forgotten.output["changed"] is True
        assert len(remaining.output["memories"]) == 9
        active = await repository.active("user-1")
        target = next(item for item in active if item.key.value == "goal.target_weight")
        height = next(item for item in active if item.key.value == "profile.height")
        dietary = next(item for item in active if item.key.value == "constraint.dietary")
        assert target.value == {"grams": 65_000, "target_date": None}
        assert height.value == {"millimeters": 1790}
        assert dietary.value["statement"] == "我对花生过敏"
        assert dietary.review_after is not None
        cleared = await handlers.clear_user_memories(
            context.model_copy(
                update={
                    "tool_call_id": "call-clear",
                    "execution_idempotency_key": "execution-clear",
                }
            ),
            ClearUserMemoriesArguments(
                scope="profile_goal_constraint",
                evidence_excerpt="清空我的个性化记忆",
            ),
        )
        replayed_clear = await handlers.clear_user_memories(
            context.model_copy(
                update={
                    "tool_call_id": "call-clear",
                    "execution_idempotency_key": "execution-clear",
                }
            ),
            ClearUserMemoriesArguments(
                scope="profile_goal_constraint",
                evidence_excerpt="清空我的个性化记忆",
            ),
        )
        assert cleared.status is ToolResultStatus.SUCCEEDED
        assert cleared.output["revoked_count"] == 9
        assert replayed_clear.output["revoked_count"] == 9
        assert await repository.active("user-1") == ()
    finally:
        await database.close()


async def test_memory_tool_rejects_non_source_evidence(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'missing-source.sqlite3'}")
    await database.create_schema()
    handlers = MemoryToolHandlers(MemoryRepository(database))
    context = ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        user_id="user-1",
        agent_version_id="agent-1",
        execution_mode=ToolExecutionMode.LIVE,
        execution_idempotency_key="execution-1",
    )
    try:
        result = await handlers.set_coaching_profile(
            context,
            SetCoachingProfileArguments(
                preferred_name="阿杰",
                evidence_excerpt="叫我阿杰",
            ),
        )
        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "missing_memory_execution_identity"
    finally:
        await database.close()
