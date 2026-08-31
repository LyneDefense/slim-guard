from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.harness.events import ItemStatus, ItemType, TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolGateway
from slim_guard.tools.meal import (
    GetRecentMealsArguments,
    MealFoodArguments,
    MealToolHandlers,
    RecordMealArguments,
    meal_tool_definitions,
    meal_tool_executors,
)
from slim_guard.tools.policy import DefaultToolPolicy, ToolAuthorization
from slim_guard.tools.registry import ToolRegistry

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


async def prepare(
    tmp_path: Path,
) -> tuple[Database, MealRepository, ToolContext]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'meal-tools.sqlite3'}")
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
            inputs=(TurnInput.user_message(text="午饭鸡胸肉和半碗米饭"),),
        )
    )
    return (
        database,
        MealRepository(database),
        ToolContext(
            thread_id=initialized.thread.id,
            turn_id=initialized.turn.id,
            tool_call_id="call-meal",
            user_id="user-1",
            agent_version_id=manifest.version_id,
            execution_mode=ToolExecutionMode.EVALUATION,
            source_item_id=initialized.source_item_id,
            execution_idempotency_key="tool-meal-1",
        ),
    )


async def test_record_and_read_meal_tools(tmp_path: Path) -> None:
    database, repository, context = await prepare(tmp_path)
    handlers = MealToolHandlers(repository, clock=lambda: NOW)
    try:
        recorded = await handlers.record_meal(
            context,
            RecordMealArguments(
                meal_type="lunch",
                foods=[
                    MealFoodArguments(name="鸡胸肉", portion="一份"),
                    MealFoodArguments(name="米饭", portion="半碗"),
                ],
            ),
        )
        recent = await handlers.get_recent_meals(
            context.model_copy(update={"tool_call_id": "call-recent-meals"}),
            GetRecentMealsArguments(limit=10),
        )

        assert recorded.status is ToolResultStatus.SUCCEEDED
        assert recorded.output["created"] is True
        assert recorded.output["foods"][1] == {"name": "米饭", "portion": "半碗"}
        assert recent.status is ToolResultStatus.SUCCEEDED
        assert recent.output["records"][0]["meal_type"] == "lunch"
        assert recent.source_ids == recorded.source_ids
    finally:
        await database.close()


async def test_record_meal_requires_gateway_execution_identity(tmp_path: Path) -> None:
    database, repository, context = await prepare(tmp_path)
    handlers = MealToolHandlers(repository, clock=lambda: NOW)
    try:
        result = await handlers.record_meal(
            context.model_copy(update={"execution_idempotency_key": None}),
            RecordMealArguments(
                foods=[MealFoodArguments(name="苹果", portion="一个")]
            ),
        )

        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "missing_execution_identity"
    finally:
        await database.close()


async def test_record_meal_accepts_real_json_array_through_gateway(
    tmp_path: Path,
) -> None:
    database, repository, context = await prepare(tmp_path)
    definitions = meal_tool_definitions()
    gateway = ToolGateway(
        registry=ToolRegistry(definitions),
        executors=meal_tool_executors(repository, clock=lambda: NOW),
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    try:
        execution = await gateway.execute(
            call=NormalizedToolCall(
                id=context.tool_call_id,
                name="record_meal",
                arguments={
                    "meal_type": "lunch",
                    "foods": [
                        {"name": "鸡胸肉", "portion": "一份"},
                        {"name": "米饭", "portion": "半碗"},
                    ],
                },
            ),
            context=context.model_copy(update={"execution_idempotency_key": None}),
            authorization=ToolAuthorization(
                allowed_tool_names=frozenset({"record_meal"}),
                isolated_write_environment=True,
            ),
        )

        assert execution.result.status is ToolResultStatus.SUCCEEDED
        assert execution.canonical_arguments is not None
        assert execution.canonical_arguments["foods"] == [
            {"name": "鸡胸肉", "portion": "一份"},
            {"name": "米饭", "portion": "半碗"},
        ]
        assert len(await repository.recent("user-1", limit=10)) == 1
    finally:
        await database.close()


async def test_uncertain_visual_meal_requires_model_grounded_user_confirmation(
    tmp_path: Path,
) -> None:
    database, repository, context = await prepare(tmp_path)
    state = HarnessStateRepository(database)
    await state.append_item(
        turn_id=context.turn_id,
        item_type=ItemType.TOOL_RESULT,
        status=ItemStatus.COMPLETED,
        payload={
            "tool_name": "inspect_image",
            "execution": {
                "tool_name": "inspect_image",
                "result": {
                    "status": "succeeded",
                    "output": {
                        "asset_id": "asset-1",
                        "description": "方形食物可能是蒸蛋或豆腐。",
                        "requires_user_confirmation": True,
                    },
                },
            },
        },
    )
    handlers = MealToolHandlers(repository, clock=lambda: NOW)
    try:
        blocked = await handlers.record_meal(
            context,
            RecordMealArguments(
                meal_type="lunch",
                foods=[MealFoodArguments(name="蒸蛋或豆腐")],
            ),
        )
        confirmed = await handlers.record_meal(
            context.model_copy(
                update={
                    "tool_call_id": "call-confirmed-meal",
                    "execution_idempotency_key": "tool-meal-confirmed",
                }
            ),
            RecordMealArguments(
                meal_type="lunch",
                foods=[MealFoodArguments(name="蒸蛋")],
                visual_confirmation="confirmed_by_current_user",
            ),
        )

        assert blocked.status is ToolResultStatus.FAILED
        assert blocked.failure is not None
        assert blocked.failure.code == "visual_confirmation_required"
        assert confirmed.status is ToolResultStatus.SUCCEEDED
        assert confirmed.output["foods"] == [{"name": "蒸蛋", "portion": None}]
    finally:
        await database.close()
