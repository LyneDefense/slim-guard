from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemType, TurnStatus
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.termination import HarnessTermination
from slim_guard.tools.contracts import ToolExecutionMode
from slim_guard.tools.weight import (
    GET_RECENT_WEIGHT_TREND_TOOL_NAME,
    RECORD_WEIGHT_TOOL_NAME,
)

FIXED_NOW = datetime(2026, 8, 27, 7, 30, tzinfo=UTC)


def tool_call(call_id: str, name: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


def final_reply(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


async def prepare_database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent-runtime.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=FIXED_NOW,
                last_seen_at=FIXED_NOW,
            )
        )
    return database


async def test_runtime_composes_complete_weight_tool_loop(tmp_path: Path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-record",
                RECORD_WEIGHT_TOOL_NAME,
                {"value": 77.6, "unit": "kg", "condition": "fasting"},
            ),
            tool_call(
                "call-trend",
                GET_RECENT_WEIGHT_TREND_TOOL_NAME,
                {"limit": 7},
            ),
            final_reply("已记录今天空腹体重 77.6kg。这是第一条记录，先建立基线。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            model_parameters={"max_output_tokens": 512, "temperature": 0},
            code_revision="test-revision",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="  今天早上空腹 77.6kg  ",
                source_message_id="wecom-message-1",
                channel_id="wecom-kf",
                occurred_at=FIXED_NOW,
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        trend = await WeightRepository(database).recent_trend("user-1")
        items = await HarnessStateRepository(database).list_items(
            result.turn_id
        )
        stored_turn = await HarnessStateRepository(database).get_turn(
            result.turn_id
        )
        stored_manifest = await AgentVersionRepository(database).get(
            runtime.manifest.version_id
        )

        assert result.termination is HarnessTermination.FINAL_RESPONSE
        assert result.final_text == "已记录今天空腹体重 77.6kg。这是第一条记录，先建立基线。"
        assert stored_manifest is not None
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.COMPLETED
        assert len(trend.records) == 1
        assert trend.records[0].weight_grams == 77_600
        assert trend.records[0].source_item_id == items[0].id
        assert [item.item_type for item in items] == [
            ItemType.USER_MESSAGE,
            ItemType.CONTEXT_SNAPSHOT,
            ItemType.MODEL_MESSAGE,
            ItemType.TOOL_CALL,
            ItemType.TOOL_RESULT,
            ItemType.MODEL_MESSAGE,
            ItemType.TOOL_CALL,
            ItemType.TOOL_RESULT,
            ItemType.MODEL_MESSAGE,
            ItemType.AGENT_MESSAGE,
        ]
        assert items[0].payload == {
            "channel_id": "wecom-kf",
            "occurred_at": FIXED_NOW.isoformat(),
            "source_message_id": "wecom-message-1",
            "text": "今天早上空腹 77.6kg",
        }
        assert [
            item.payload["tool_name"]
            for item in items
            if item.item_type is ItemType.TOOL_CALL
        ] == [RECORD_WEIGHT_TOOL_NAME, GET_RECENT_WEIGHT_TREND_TOOL_NAME]
        assert model.requests[0].messages[0].content == SLIM_GUARD_HARNESS_PROMPT
        assert [tool.name for tool in model.requests[0].tools] == [
            RECORD_WEIGHT_TOOL_NAME,
            GET_RECENT_WEIGHT_TREND_TOOL_NAME,
            "inspect_image",
            "record_meal",
            "get_recent_meals",
            "record_exercise",
            "get_recent_exercise",
            "configure_checkin_schedule",
            "get_checkin_schedule",
        ]
        first_observation = json.loads(model.requests[1].messages[-1].content or "")
        second_observation = json.loads(model.requests[2].messages[-1].content or "")
        assert first_observation["status"] == "succeeded"
        assert first_observation["output"]["weight_kg"] == "77.6"
        assert second_observation["status"] == "succeeded"
        assert second_observation["output"]["direction"] == "unknown"
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()
