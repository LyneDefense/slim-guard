from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT
from slim_guard.agent.runtime import AgentRuntimeRequest, AgentScheduledRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.agent_models.vision import (
    VisionCertainty,
    VisionInspectionRequest,
    VisionInspectionResponse,
    VisionObservation,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.body_fat.repository import BodyFatRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.termination import HarnessTermination
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.body_fat import RECORD_BODY_FAT_TOOL_NAME
from slim_guard.tools.contracts import ToolExecutionMode
from slim_guard.tools.meal import RECORD_MEAL_TOOL_NAME
from slim_guard.tools.memory import (
    CLEAR_USER_MEMORIES_TOOL_NAME,
    SET_BODY_FAT_GOAL_TOOL_NAME,
    SET_BODY_PROFILE_TOOL_NAME,
    SET_COACHING_PROFILE_TOOL_NAME,
    SET_CONVERSATION_HANDOFF_TOOL_NAME,
    SET_EXERCISE_PROFILE_TOOL_NAME,
    SET_WEIGHT_GOAL_TOOL_NAME,
)
from slim_guard.tools.pending import RESOLVE_PENDING_USER_ACTION_TOOL_NAME
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


class MemoryClearModelGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._step = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = self._step
        self._step += 1
        if step == 0:
            return tool_call(
                "call-profile",
                SET_COACHING_PROFILE_TOOL_NAME,
                {"preferred_name": "阿杰", "evidence_excerpt": "以后叫我阿杰"},
            )
        if step == 1:
            return final_reply("记住了，以后叫你阿杰。")
        if step == 2:
            return tool_call(
                "call-clear",
                CLEAR_USER_MEMORIES_TOOL_NAME,
                {
                    "scope": "profile_goal_constraint",
                    "evidence_excerpt": "清空我的个性化记忆",
                },
            )
        if step == 3:
            context = next(
                message.content or ""
                for message in request.messages
                if "近期对话工作记忆" in (message.content or "")
                and "pending_user_confirmations" in (message.content or "")
            )
            match = re.search(r'"action_id":"([^"]+)"', context)
            assert match is not None
            return tool_call(
                "call-resolve-clear",
                RESOLVE_PENDING_USER_ACTION_TOOL_NAME,
                {
                    "action_id": match.group(1),
                    "decision": "approve",
                    "evidence_excerpt": "确认执行",
                },
            )
        if step == 4:
            return final_reply("个性化记忆已经清空；体重、饮食和运动记录未删除。")
        raise AssertionError(f"Unexpected model call: {step}")

    async def close(self) -> None:
        return None

    def assert_exhausted(self) -> None:
        assert self._step == 5


class MealImageFollowupModelGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.asset_id: str | None = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = len(self.requests)
        if step == 1:
            image_input = json.loads(request.messages[-1].content or "")
            self.asset_id = image_input["asset_id"]
            return tool_call(
                "call-inspect-first",
                "inspect_image",
                {"asset_id": self.asset_id, "focus": "meal"},
            )
        if step == 2:
            return final_reply("图片里有几样食物不确定，请告诉我具体是什么。")
        if step == 3:
            assert self.asset_id is not None
            working = next(
                message.content or ""
                for message in request.messages
                if "近期对话工作记忆" in (message.content or "")
            )
            assert self.asset_id in working
            assert '"requires_user_confirmation":true' in working
            return tool_call(
                "call-inspect-recent",
                "inspect_image",
                {"asset_id": self.asset_id, "focus": "meal"},
            )
        if step == 4:
            return final_reply("明白是午餐；请确认方形块、红色条状物和黑色食物。")
        if step == 5:
            return tool_call(
                "call-record-confirmed-meal",
                RECORD_MEAL_TOOL_NAME,
                {
                    "meal_type": "lunch",
                    "foods": [
                        {"name": "白米饭"},
                        {"name": "炒白菜"},
                        {"name": "蒸蛋"},
                        {"name": "火腿肠"},
                        {"name": "油豆腐"},
                        {"name": "木耳"},
                    ],
                    "visual_confirmation": "confirmed_by_current_user",
                },
            )
        if step == 6:
            return final_reply("午餐已经记录。")
        raise AssertionError(f"Unexpected model call: {step}")

    async def close(self) -> None:
        return None


class UncertainMealVisionGateway:
    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        return VisionInspectionResponse(
            category="meal",
            description="白米饭、炒白菜和几样需要确认的配菜。",
            observations=(
                VisionObservation(
                    label="主食",
                    detail="清晰可见白米饭",
                    certainty=VisionCertainty.CLEAR,
                ),
                VisionObservation(
                    label="方形配菜",
                    detail="可能是蒸蛋或豆腐",
                    certainty=VisionCertainty.UNCERTAIN,
                ),
            ),
            requires_user_confirmation=True,
        )

    async def close(self) -> None:
        return None


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
                "record_body_fat",
                "get_recent_body_fat_trend",
                "inspect_image",
            "record_meal",
            "get_recent_meals",
            "record_exercise",
            "get_recent_exercise",
            "configure_checkin_schedule",
            "get_checkin_schedule",
            "update_record_status",
            "set_coaching_profile",
                "set_body_profile",
                "set_exercise_profile",
            "upsert_food_preference",
            "upsert_exercise_preference",
                "set_weight_goal",
                "set_body_fat_goal",
            "set_behavior_goal",
            "record_user_constraint",
            "list_user_memories",
            "forget_user_memory",
            "set_conversation_handoff",
            "resolve_conversation_handoff",
            "clear_user_memories",
            "resolve_pending_user_action",
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


async def test_runtime_persists_and_recalls_profile_memory_across_turns(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-profile",
                SET_COACHING_PROFILE_TOOL_NAME,
                {
                    "preferred_name": "阿杰",
                    "response_style": "concise",
                    "evidence_excerpt": "以后叫我阿杰，回复简短点",
                },
            ),
            final_reply("记住了，阿杰。之后我会简短回复。"),
            final_reply("状态不错，继续按计划记录。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            model_parameters={"max_output_tokens": 512},
            code_revision="test-memory",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        first = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="以后叫我阿杰，回复简短点",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        second = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="今天状态怎么样？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        memory_context = next(
            message.content or ""
            for message in model.requests[2].messages
            if "权威用户事实" in (message.content or "")
        )

        assert first.final_text == "记住了，阿杰。之后我会简短回复。"
        assert second.final_text == "状态不错，继续按计划记录。"
        assert '"key":"identity.preferred_name"' in memory_context
        assert '"name":"阿杰"' in memory_context
        assert '"style":"concise"' in memory_context
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_saves_height_as_profile_memory_not_weight_record(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-height",
                SET_BODY_PROFILE_TOOL_NAME,
                {
                    "height_value": 159,
                    "evidence_excerpt": "我身高159",
                },
            ),
            final_reply("记住了，你的身高是 159cm。"),
            final_reply("你保存的身高是 159cm。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-height-memory",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="我身高159",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="我的身高是多少？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        memory_context = next(
            message.content or ""
            for message in model.requests[2].messages
            if "权威用户事实" in (message.content or "")
        )
        memories = await MemoryRepository(database).active("user-1")
        weights = await WeightRepository(database).recent_trend("user-1")

        assert '"key":"profile.height"' in memory_context
        assert '"millimeters":1590' in memory_context
        assert [memory.value for memory in memories if memory.key.value == "profile.height"] == [
            {"millimeters": 1590}
        ]
        assert weights.records == ()
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_handles_onboarding_measurements_goals_and_habits(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        NormalizedToolCall(
                            id="call-weight",
                            name=RECORD_WEIGHT_TOOL_NAME,
                            arguments={"value": 58},
                        ),
                        NormalizedToolCall(
                            id="call-weight-goal",
                            name=SET_WEIGHT_GOAL_TOOL_NAME,
                            arguments={
                                "value": 55,
                                "evidence_excerpt": "目标55",
                            },
                        ),
                        NormalizedToolCall(
                            id="call-body-fat",
                            name=RECORD_BODY_FAT_TOOL_NAME,
                            arguments={"value": 31},
                        ),
                        NormalizedToolCall(
                            id="call-body-fat-goal",
                            name=SET_BODY_FAT_GOAL_TOOL_NAME,
                            arguments={
                                "value": 24,
                                "evidence_excerpt": "目标24%",
                            },
                        ),
                        NormalizedToolCall(
                            id="call-health",
                            name="record_user_constraint",
                            arguments={
                                "category": "health_context",
                                "subject": "胰岛素抵抗",
                                "statement": "我有胰岛素抵抗",
                                "evidence_excerpt": "我有胰岛素抵抗",
                            },
                        ),
                        NormalizedToolCall(
                            id="call-exercise-profile",
                            name=SET_EXERCISE_PROFILE_TOOL_NAME,
                            arguments={
                                "habit_summary": "不运动",
                                "evidence_excerpt": "不运动",
                            },
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            final_reply(
                "已记录体重58kg、体脂31%，并记住目标体重55kg、目标体脂24%、"
                "你自述有胰岛素抵抗以及目前不运动。"
            ),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-onboarding-facts",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="体重58 目标55 体脂31% 目标24% 我有胰岛素抵抗 不运动",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        weights = await WeightRepository(database).recent_trend("user-1")
        body_fat = await BodyFatRepository(database).recent_trend("user-1")
        memories = await MemoryRepository(database).active("user-1")
        values = {memory.key.value: memory.value for memory in memories}

        assert result.final_text.startswith("已记录体重58kg、体脂31%")
        assert weights.current is not None and weights.current.weight_grams == 58_000
        assert body_fat.current is not None and body_fat.current.basis_points == 3100
        assert values["goal.target_weight"]["grams"] == 55_000
        assert values["goal.target_body_fat"]["basis_points"] == 2400
        assert values["profile.exercise_habit"]["statement"] == "不运动"
        assert values["constraint.health_context"]["subject"] == "胰岛素抵抗"
        assert "constraint.exercise" not in values
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_preserves_truthful_partial_success_reply(tmp_path: Path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        NormalizedToolCall(
                            id="call-habit",
                            name=SET_EXERCISE_PROFILE_TOOL_NAME,
                            arguments={
                                "habit_summary": "目前不运动",
                                "evidence_excerpt": "目前不运动",
                            },
                        ),
                        NormalizedToolCall(
                            id="call-invalid-goal",
                            name=SET_WEIGHT_GOAL_TOOL_NAME,
                            arguments={
                                "value": 55,
                                "evidence_excerpt": "这里没有对应数字",
                            },
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            final_reply("已记住你目前不运动；目标体重没有保存成功，请再告诉我一次。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-partial-memory-result",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="目前不运动，目标55",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        items = await HarnessStateRepository(database).list_items(result.turn_id)

        assert result.final_text == (
            "已记住你目前不运动；目标体重没有保存成功，请再告诉我一次。"
        )
        assert all(item.item_type is not ItemType.OUTPUT_GUARD for item in items)
    finally:
        await model.close()
        await database.close()


async def test_runtime_does_not_claim_failed_memory_write_succeeded(tmp_path: Path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-profile",
                SET_COACHING_PROFILE_TOOL_NAME,
                {
                    "preferred_name": "阿杰",
                    "evidence_excerpt": "用户没有说过的证据",
                },
            ),
            final_reply("记住了，以后叫你阿杰。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-memory-guard",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="以后叫我阿杰",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        items = await HarnessStateRepository(database).list_items(result.turn_id)

        assert result.final_text == (
            "这项记忆没有确认保存或撤销成功，请把你的要求再说一次，我会重新处理。"
        )
        guard = next(item for item in items if item.item_type is ItemType.OUTPUT_GUARD)
        assert guard.payload["code"] == "failed_memory_write_success_claim"
    finally:
        await model.close()
        await database.close()


async def test_runtime_recalls_weight_goal_without_creating_measurement(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-weight-goal",
                SET_WEIGHT_GOAL_TOOL_NAME,
                {
                    "value": 65.0,
                    "unit": "kg",
                    "evidence_excerpt": "我的目标是65kg",
                },
            ),
            final_reply("已记下你的自述目标：65kg。"),
            final_reply("目标仍是65kg，我们按实际趋势稳步观察。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-weight-goal",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="我的目标是65kg",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="我的目标还记得吗？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        memory_context = next(
            message.content or ""
            for message in model.requests[2].messages
            if "权威用户事实" in (message.content or "")
        )
        trend = await WeightRepository(database).recent_trend("user-1")

        assert '"key":"goal.target_weight"' in memory_context
        assert '"grams":65000' in memory_context
        assert trend.records == ()
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_supplies_recent_visible_dialogue_on_the_next_turn(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            final_reply("先从一周三次快走开始，每次二十分钟。"),
            final_reply("可以，刚才那个计划的下一步是安排具体日期。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-working-memory",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="帮我定个容易开始的运动计划",
                execution_mode=ToolExecutionMode.EVALUATION,
            )
        )
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="刚才那个继续",
                execution_mode=ToolExecutionMode.EVALUATION,
            )
        )
        working_context = next(
            message.content or ""
            for message in model.requests[1].messages
            if "近期对话工作记忆" in (message.content or "")
        )

        assert '"role":"user"' in working_context
        assert "帮我定个容易开始的运动计划" in working_context
        assert "先从一周三次快走开始，每次二十分钟。" in working_context
        assert "刚才那个继续" not in working_context
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_carries_real_image_reference_until_user_confirms_meal(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = MealImageFollowupModelGateway()
    vision = UncertainMealVisionGateway()
    runtime = build_agent_runtime(
        database=database,
        model=model,
        vision=vision,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-recent-image-meal",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        first = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                image_bytes=b"\x89PNG\r\n\x1a\nmeal",
                image_mime_type="image/png",
                source_message_id="meal-image-1",
                channel_id="wecom-kf",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        second = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="这个是我中午吃的",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        before_confirmation = await MealRepository(database).recent("user-1")
        third = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="方形块是蒸蛋，红色条状物是火腿肠，黑色的是木耳",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        meals = await MealRepository(database).recent("user-1")

        assert first.final_text == "图片里有几样食物不确定，请告诉我具体是什么。"
        assert second.final_text == (
            "明白是午餐；请确认方形块、红色条状物和黑色食物。"
        )
        assert before_confirmation == ()
        assert third.final_text == "午餐已经记录。"
        assert len(meals) == 1
        assert meals[0].meal_type.value == "lunch"
        assert [food.name for food in meals[0].foods] == [
            "白米饭",
            "炒白菜",
            "蒸蛋",
            "火腿肠",
            "油豆腐",
            "木耳",
        ]
        assert model.asset_id is not None
        assert "meal_photo" not in json.dumps(
            [request.model_dump(mode="json") for request in model.requests],
            ensure_ascii=False,
        )
    finally:
        await model.close()
        await vision.close()
        await database.close()


async def test_runtime_persists_explicit_handoff_for_a_later_turn(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            tool_call(
                "call-handoff",
                SET_CONVERSATION_HANDOFF_TOOL_NAME,
                {
                    "objective": "制定一周饮食计划",
                    "unresolved": ["确认工作日午餐安排"],
                    "evidence_excerpt": "下次接着制定饮食计划",
                },
            ),
            final_reply("好，下次从工作日午餐安排接着做。"),
            final_reply("好，我们继续安排工作日午餐。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-handoff",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="今天先到这，下次接着制定饮食计划",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="上次那个继续",
                execution_mode=ToolExecutionMode.EVALUATION,
            )
        )
        working_context = next(
            message.content or ""
            for message in model.requests[2].messages
            if "近期对话工作记忆" in (message.content or "")
        )

        assert '"objective":"制定一周饮食计划"' in working_context
        assert '"unresolved":["确认工作日午餐安排"]' in working_context
        assert '"handoff_id":' in working_context
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_requires_and_applies_bulk_memory_clear_confirmation(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = MemoryClearModelGateway()
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-memory-clear",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="以后叫我阿杰",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        requested = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="清空我的个性化记忆",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        before_confirmation = await MemoryRepository(database).active("user-1")
        confirmed = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="确认执行",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        assert requested.termination is HarnessTermination.WAITING_USER_CONFIRMATION
        assert len(before_confirmation) == 1
        assert confirmed.final_text == (
            "个性化记忆已经清空；体重、饮食和运动记录未删除。"
        )
        assert await MemoryRepository(database).active("user-1") == ()
        model.assert_exhausted()
    finally:
        await model.close()
        await database.close()


async def test_runtime_runs_input_free_scheduled_turn_without_tools(tmp_path: Path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway((final_reply("早上好，记得空腹称一下体重哦。"),))
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            model_parameters={"max_output_tokens": 512},
            code_revision="test-scheduled",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_scheduled(
            AgentScheduledRequest(
                user_id="user-1",
                trigger=TurnTrigger.WEIGHT_REMINDER,
                execution_mode=ToolExecutionMode.EVALUATION,
            )
        )

        items = await HarnessStateRepository(database).list_items(result.turn_id)
        assert result.final_text == "早上好，记得空腹称一下体重哦。"
        assert [item.item_type for item in items] == [
            ItemType.CONTEXT_SNAPSHOT,
            ItemType.MODEL_MESSAGE,
            ItemType.AGENT_MESSAGE,
        ]
        assert model.requests[0].tools == ()
        assert '"trigger":"weight_reminder"' in (
            model.requests[0].messages[1].content or ""
        )
    finally:
        await model.close()
        await database.close()


async def test_runtime_blocks_tools_and_replaces_emergency_model_output(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway((final_reply("没关系，继续运动观察一下。"),))
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="test-safety",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="我运动后胸痛而且呼吸困难",
                execution_mode=ToolExecutionMode.EVALUATION,
            )
        )
        items = await HarnessStateRepository(database).list_items(result.turn_id)

        assert result.termination is HarnessTermination.FINAL_RESPONSE
        assert "尽快联系当地急救服务或前往急诊" in (result.final_text or "")
        assert model.requests[0].tools == ()
        assert any(item.item_type is ItemType.OUTPUT_GUARD for item in items)
        guard_item = next(
            item for item in items if item.item_type is ItemType.OUTPUT_GUARD
        )
        assert guard_item.payload["code"] == "medical_emergency_escalation"
    finally:
        await model.close()
        await database.close()
