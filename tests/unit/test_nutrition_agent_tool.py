from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.agents.nutrition import CONSULT_NUTRITION_TOOL_NAME
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.runtime.contracts import AgentRole
from slim_guard.tools.contracts import ToolExecutionMode
from tests.conversation_script import SystemConversationScript

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


async def prepare_database(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'nutrition-agent-tool.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    return database


def tool_call() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id="consult-1",
                    name=CONSULT_NUTRITION_TOOL_NAME,
                    arguments={"professional_question": "减脂期间晚餐应该怎样搭配？"},
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


async def test_core_invokes_nutrition_with_default_tool_availability(tmp_path) -> None:
    database = await prepare_database(tmp_path)
    assessment = {
        "assessment_type": "general",
        "overall": "当前资料库没有足够证据支持具体搭配结论。",
        "uncertainty_note": "需要补充可核对的营养资料后再判断。",
    }
    model = ScriptedModelGateway(
        (
            tool_call(),
            text_response(json.dumps(assessment, ensure_ascii=False)),
            text_response("目前资料还不够，我先不下具体结论。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=SystemConversationScript(model),
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="nutrition-agent-tool-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="减脂期间晚餐应该怎么搭配？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        invocations = await OrchestrationRepository(database).list_turn_invocations(result.turn_id)
        core = next(item for item in invocations if item.agent_role == AgentRole.CORE.value)
        nutrition = next(
            item for item in invocations if item.agent_role == AgentRole.NUTRITION_EXPERT.value
        )

        assert result.final_text == "目前资料还不够，我先不下具体结论。"
        assert nutrition.parent_invocation_id == core.invocation_id
        assert nutrition.caller == AgentRole.CORE.value
        assert nutrition.output_schema == "ProfessionalAssessment"
        assert model.requests[1].purpose is ModelPurpose.NUTRITION
        observation = json.loads(model.requests[2].messages[-1].content or "")
        assert observation["output"]["assessment"]["overall"] == assessment["overall"]
    finally:
        await database.close()


async def test_available_nutrition_tool_does_not_trigger_automatic_consultation(tmp_path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway((text_response("你好。"),))
    runtime = build_agent_runtime(
        database=database,
        model=SystemConversationScript(model),
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="nutrition-available-not-automatic-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(user_id="user-1", text="你好"),
        )
        assert result.final_text == "你好。"
        assert len(model.requests) == 1
        assert CONSULT_NUTRITION_TOOL_NAME in {tool.name for tool in model.requests[0].tools}
        assert model.requests[0].purpose is ModelPurpose.HARNESS_TURN
        invocations = await OrchestrationRepository(database).list_turn_invocations(result.turn_id)
        assert len(invocations) == 2
        by_role = {item.agent_role: item for item in invocations}
        assert set(by_role) == {AgentRole.CORE.value, AgentRole.CONVERSATION.value}
        assert by_role[AgentRole.CONVERSATION.value].parent_invocation_id == (
            by_role[AgentRole.CORE.value].invocation_id
        )
    finally:
        await database.close()
