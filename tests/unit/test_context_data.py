from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.exercise.contracts import ExerciseRecordCommand
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.contracts import MealFood, MealRecordCommand, MealType
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.weight.contracts import (
    WeightMeasurementCommand,
    WeightMeasurementCondition,
    WeightUnit,
)
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.context_data import AuthoritativeContextDataProvider
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

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


async def test_provider_loads_bounded_authoritative_user_facts(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'context.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                nickname="小明",
                first_seen_at=NOW,
                last_seen_at=NOW,
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
        memory_policy_version="domain-records-v1",
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
            inputs=(TurnInput.user_message(text="今天打卡"),),
        )
    )
    assert initialized.source_item_id is not None
    source = {
        "source_turn_id": initialized.turn.id,
        "source_item_id": initialized.source_item_id,
    }
    weights = WeightRepository(database)
    meals = MealRepository(database)
    exercise = ExerciseRepository(database)
    await weights.record(
        WeightMeasurementCommand(
            user_id="user-1",
            value=Decimal("77.6"),
            unit=WeightUnit.KG,
            measured_at=NOW,
            condition=WeightMeasurementCondition.FASTING,
            idempotency_key="weight-1",
            source_tool_call_id="weight-call",
            **source,
        )
    )
    await meals.record(
        MealRecordCommand(
            user_id="user-1",
            meal_type=MealType.LUNCH,
            foods=(MealFood(name="鸡胸肉", portion="一份"),),
            occurred_at=NOW,
            idempotency_key="meal-1",
            source_tool_call_id="meal-call",
            **source,
        )
    )
    await exercise.record(
        ExerciseRecordCommand(
            user_id="user-1",
            activity_name="快走",
            duration_minutes=30,
            occurred_at=NOW,
            idempotency_key="exercise-1",
            source_tool_call_id="exercise-call",
            **source,
        )
    )
    provider = AuthoritativeContextDataProvider(
        database=database,
        weights=weights,
        meals=meals,
        exercise=exercise,
    )
    try:
        context = await provider.load(user_id="user-1", current_time=NOW)

        assert context["profile"] == {
            "nickname": "小明",
            "first_seen_at": NOW.isoformat(),
        }
        assert context["recent_weights"] == [
            {
                "weight_kg": "77.6",
                "measured_at": NOW.isoformat(),
                "condition": "fasting",
            }
        ]
        assert context["recent_meals"][0]["foods"] == [
            {"name": "鸡胸肉", "portion": "一份"}
        ]
        assert context["recent_exercise"][0]["duration_minutes"] == 30
    finally:
        await database.close()
