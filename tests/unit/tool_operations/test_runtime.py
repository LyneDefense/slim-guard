"""Exercise the assembled Harness with real tools/storage and a scripted model."""

import json

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole
from slim_guard.features.health_records.meal.repository import MealRepository
from slim_guard.harness.events import ItemType
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.termination import HarnessTermination
from slim_guard.tools.contracts import ToolExecutionMode
from tests.conversation_script import SystemConversationScript
from tests.unit.test_agent_runtime import FIXED_NOW, final_reply, prepare_database, tool_call


async def test_real_harness_corrects_foods_in_same_turn_and_persists_one_meal(tmp_path):
    database = await prepare_database(tmp_path)
    core = ScriptedModelGateway(
        (
            tool_call("invalid", "record_meal", {"meal_type": "lunch", "foods": ["烧茄子"]}),
            tool_call(
                "corrected", "record_meal", {"meal_type": "lunch", "foods": [{"name": "烧茄子"}]}
            ),
            final_reply("午餐已记录：烧茄子。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=SystemConversationScript(core),
        clock=lambda: FIXED_NOW,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="test-tool-recovery",
        ),
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="这是今天午餐，烧茄子，帮我记一下。",
                source_message_id="recovery-test",
                channel_id="test",
                occurred_at=FIXED_NOW,
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        assert result.termination is HarnessTermination.FINAL_RESPONSE
        error = json.loads(
            next(m.content for m in core.requests[1].messages if m.role is MessageRole.TOOL)
        )
        assert error["failure"]["code"] == "invalid_arguments"
        assert error["failure"]["effect_status"] == "not_applied"
        assert error["failure"]["field_errors"][0]["path"] == ["foods", 0]
        assert core.requests[1].tools == core.requests[0].tools
        records = await MealRepository(database).recent("user-1")
        assert len(records) == 1
        assert records[0].occurred_at == FIXED_NOW
        assert records[0].idempotency_key.startswith("op-")
        items = await HarnessStateRepository(database).list_items(result.turn_id)
        messages = [i.payload for i in items if i.item_type is ItemType.AGENT_MESSAGE]
        assert sum(m["kind"] == "card" for m in messages) == 1
    finally:
        await database.close()
