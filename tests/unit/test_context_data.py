from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from slim_guard.db.models import MobileCoachProfileRecord, SlimGuardUser
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
from slim_guard.memory.contracts import MemoryFactInput, MemoryKey, MemoryWriteCommand
from slim_guard.memory.repository import MemoryRepository
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
        session.add(
            MobileCoachProfileRecord(
                user_id="user-1",
                age_band="30_39",
                height_millimeters=1680,
                current_weight_grams=77600,
                weight_measured_on=NOW.date(),
                goal_type="lose_weight",
                target_weight_grams=68000,
                target_date=(NOW + timedelta(days=120)).date(),
                current_body_fat_basis_points=2560,
                target_body_fat_basis_points=2200,
                exercise_frequency="weekly_1_2",
                completed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
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
            inputs=(TurnInput.user_message(text="今天打卡，我对花生过敏"),),
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
    memories = MemoryRepository(database, clock=lambda: NOW)
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
    await memories.write(
        MemoryWriteCommand(
            user_id="user-1",
            facts=(
                MemoryFactInput(
                    key=MemoryKey.DIETARY_CONSTRAINT,
                    value={"subject": "花生", "statement": "我对花生过敏"},
                ),
            ),
            evidence_excerpt="我对花生过敏",
            operation_id="memory-1",
            source_turn_id=initialized.turn.id,
            source_item_id=initialized.source_item_id,
            source_tool_call_id="memory-call",
        )
    )
    provider = AuthoritativeContextDataProvider(
        database=database,
        weights=weights,
        meals=meals,
        exercise=exercise,
        memories=memories,
    )
    try:
        context = await provider.load(
            user_id="user-1",
            current_time=NOW + timedelta(days=181),
            trigger=TurnTrigger.DAILY_REVIEW,
        )

        assert context["profile"] == {
            "nickname": "小明",
            "first_seen_at": NOW.isoformat(),
            "coach_profile": {
                "source": "user_completed_mobile_profile",
                "schema_version": 1,
                "revision": 1,
                "age_band": "30_39",
                "weight_assessment_standard": "adult",
                "height_cm": "168",
                "current_weight_kg": "77.6",
                "weight_measured_on": NOW.date().isoformat(),
                "goal_type": "lose_weight",
                "target_weight_kg": "68",
                "target_date": (NOW + timedelta(days=120)).date().isoformat(),
                "current_body_fat_percent": "25.6",
                "target_body_fat_percent": "22",
                "exercise_frequency": "weekly_1_2",
                "completed_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            },
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
        assert context["profile_memory"][0]["key"] == "constraint.dietary"
        assert context["profile_memory"][0]["sensitivity"] == "health"
        assert context["profile_memory"][0]["stale"] is True

        core_context = await AuthoritativeContextDataProvider(
            database=database,
            weights=weights,
            meals=meals,
            exercise=exercise,
            memories=memories,
            preload_domain_history=False,
        ).load(
            user_id="user-1",
            current_time=NOW + timedelta(days=181),
            trigger=TurnTrigger.USER_MESSAGE,
        )
        assert core_context["profile"] == context["profile"]
        assert core_context["profile_memory"] == context["profile_memory"]
        assert "recent_weights" not in core_context
        assert "recent_body_fat" not in core_context
        assert "recent_meals" not in core_context
        assert "recent_exercise" not in core_context
    finally:
        await database.close()
