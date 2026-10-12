"""Real runtime/tool/persistence integration; a deterministic model selects actions."""

import json
from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.gateway import MessageRole, ModelRequest, ModelResponse
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.features.health_records.meal.repository import MealRepository
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.tasks.repository import ConversationTaskRepository
from slim_guard.tools.contracts import ToolExecutionMode
from tests.conversation_script import SystemConversationScript
from tests.unit.test_agent_runtime import final_reply, tool_call

NOW = datetime(2026, 9, 23, 8, tzinfo=UTC)


class ClarificationScript:
    def __init__(self):
        self.index = 0
        self.task: dict = {}
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        for message in request.messages:
            if message.role is MessageRole.TOOL:
                observation = json.loads(message.content or "{}")
                assert observation["status"] == "succeeded", observation
                if task := observation.get("output", {}).get("task"):
                    self.task = task
        index = self.index
        self.index += 1
        if index in {0, 2, 4, 6}:
            text = {0: "这是午餐", 2: "荷包蛋", 4: "烧茄子", 6: "烧茄子"}[index]
            args = {
                "objective": "记录午餐",
                "evidence_excerpt": text,
                "interpretation": "保留本轮补充，不覆盖先前答案",
                "unresolved": {
                    0: ["蛋的做法", "深色菜名"],
                    2: ["深色菜名"],
                    4: ["尚未执行记录"],
                    6: [],
                }[index],
                "status": "resolved" if index == 6 else "open",
            }
            if index:
                args.update(task_id=self.task["task_id"], expected_revision=self.task["revision"])
            return tool_call(f"save-{index}", "save_conversation_task", args)
        if index == 1:
            return final_reply("鸡蛋怎么做的？深色菜是什么？")
        if index == 3:
            return final_reply("深色菜是什么？")
        if index == 5:
            working = next(
                m.content for m in request.messages if "近期对话工作记忆" in (m.content or "")
            )
            assert working and "这是午餐" in working and "荷包蛋" in working
            return tool_call(
                "write-meal",
                "record_meal",
                {
                    "meal_type": "lunch",
                    "foods": [{"name": "荷包蛋"}, {"name": "烧茄子"}],
                    "evidence": {
                        "source_kind": "user_reported",
                        "source_refs": ["current-user-message"],
                    },
                },
            )
        assert index == 7
        return final_reply("已记录午餐。")

    async def close(self):
        pass


async def test_task_survives_small_context_window_and_records_once(tmp_path: Path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'task-runtime.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    core = ClarificationScript()
    runtime = build_agent_runtime(
        database=db,
        model=SystemConversationScript(core),
        clock=lambda: NOW,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="test", vision_model="test"),
            memory=MemoryRuntimeConfig(recent_turn_count=1, recent_dialogue_max_chars=100),
            code_revision="task-flow",
        ),
    )
    try:
        results = []
        for text in ("这是午餐", "荷包蛋", "烧茄子"):
            results.append(
                await runtime.run_user_message(
                    AgentRuntimeRequest(
                        user_id="user-1",
                        text=text,
                        execution_mode=ToolExecutionMode.EVALUATION,
                        isolated_write_environment=True,
                    )
                )
            )
        assert all(r.failure_code is None for r in results)
        assert core.index == 8
        assert "鸡蛋" not in (results[1].final_text or "")
        meals = await MealRepository(db).recent("user-1", limit=10)
        assert len(meals) == 1
        assert [food.name for food in meals[0].foods] == ["荷包蛋", "烧茄子"]
        tasks = await ConversationTaskRepository(db, clock=lambda: NOW).list_for_user(
            "user-1",
            at=NOW,
            status="resolved",
        )
        assert len(tasks) == 1 and tasks[0].unresolved == ()
        assert [e.excerpt for e in tasks[0].evidence] == ["这是午餐", "荷包蛋", "烧茄子", "烧茄子"]
    finally:
        await db.close()
