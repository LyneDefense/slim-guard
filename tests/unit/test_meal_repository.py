from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.meal.contracts import MealFood, MealRecordCommand, MealType
from slim_guard.domain.meal.errors import MealRecordCollision, MealSourceMismatch
from slim_guard.domain.meal.repository import MealRepository
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


async def prepare_repository(
    tmp_path: Path,
) -> tuple[Database, MealRepository, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'meals.sqlite3'}")
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
            inputs=(TurnInput.user_message(text="午饭吃了鸡胸肉和半碗米饭"),),
        )
    )
    assert initialized.source_item_id is not None
    return database, MealRepository(database), initialized.turn.id, initialized.source_item_id


def command(
    *,
    turn_id: str,
    item_id: str,
    user_id: str = "user-1",
    idempotency_key: str = "meal-execution-1",
    occurred_at: datetime = NOW,
) -> MealRecordCommand:
    return MealRecordCommand(
        user_id=user_id,
        meal_type=MealType.LUNCH,
        foods=(
            MealFood(name="鸡胸肉", portion="一份"),
            MealFood(name="米饭", portion="半碗"),
        ),
        note="用户文字记录",
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        source_turn_id=turn_id,
        source_item_id=item_id,
        source_tool_call_id="call-meal-1",
    )


async def test_meal_record_is_idempotent_and_query_is_newest_first(
    tmp_path: Path,
) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        first = await repository.record(command(turn_id=turn_id, item_id=item_id))
        repeated = await repository.record(command(turn_id=turn_id, item_id=item_id))
        later = await repository.record(
            command(
                turn_id=turn_id,
                item_id=item_id,
                idempotency_key="meal-execution-2",
                occurred_at=NOW + timedelta(hours=6),
            )
        )
        recent = await repository.recent("user-1")

        assert first.created is True
        assert repeated.created is False
        assert repeated.record == first.record
        assert [record.id for record in recent] == [later.record.id, first.record.id]
        assert first.record.foods[1].portion == "半碗"
    finally:
        await database.close()


async def test_meal_source_must_belong_to_owner(tmp_path: Path) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        with pytest.raises(MealSourceMismatch, match="another user"):
            await repository.record(
                command(turn_id=turn_id, item_id=item_id, user_id="user-2")
            )
    finally:
        await database.close()


async def test_meal_idempotency_collision_cannot_change_foods(tmp_path: Path) -> None:
    database, repository, turn_id, item_id = await prepare_repository(tmp_path)
    try:
        original = command(turn_id=turn_id, item_id=item_id)
        await repository.record(original)
        changed = original.model_copy(
            update={"foods": (MealFood(name="蛋糕", portion="两块"),)}
        )

        with pytest.raises(MealRecordCollision, match="idempotency collision"):
            await repository.record(changed)
    finally:
        await database.close()
