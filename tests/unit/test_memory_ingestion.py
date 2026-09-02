from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository, NewTurnItem
from slim_guard.memory.contracts import MemoryKey
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode
from slim_guard.tools.memory import SET_BODY_PROFILE_TOOL_NAME

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def final(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


class HeightIngestionGateway:
    def __init__(self, heights: tuple[int | None, ...]) -> None:
        self._heights = heights
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        height = self._heights[len(self.requests) - 1]
        assert request.purpose.value == "memory_ingestion"
        payload = json.loads(request.messages[-1].content or "")
        if height is None:
            return final("NO_MEMORY")
        evidence = next(
            item
            for item in reversed(payload["user_messages"])
            if "身高" in item["content"] and str(height) in item["content"]
        )
        evidence_excerpt = evidence["content"]
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    NormalizedToolCall(
                        id=f"ingest-height-{height}",
                        name=SET_BODY_PROFILE_TOOL_NAME,
                        arguments={
                            "height_value": height,
                            "height_unit": "cm",
                            "evidence_excerpt": evidence_excerpt,
                            "evidence_ref": evidence["evidence_ref"],
                        },
                    ),
                ),
            ),
            finish_reason="tool_calls",
        )

    async def close(self) -> None:
        return None


async def prepare_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / name}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    return database


def definition(code_revision: str) -> AgentRuntimeDefinition:
    return AgentRuntimeDefinition(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        code_revision=code_revision,
    )


def request(text: str) -> AgentRuntimeRequest:
    return AgentRuntimeRequest(
        user_id="user-1",
        text=text,
        execution_mode=ToolExecutionMode.EVALUATION,
        isolated_write_environment=True,
    )


async def test_model_first_ingestion_creates_and_updates_database_memory(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path, "memory-ingestion.sqlite3")
    ingestion = HeightIngestionGateway((179, 179, 178, None))
    conversation = ScriptedModelGateway(
        (
            final("记住了。"),
            final("已经记着了。"),
            final("刚量的是178cm，我已经从179cm更新好了。"),
            final("已经记着了，你身高178cm。"),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=conversation,
        memory_ingestion_model=ingestion,
        definition=definition("test-memory-ingestion"),
        clock=lambda: NOW,
    )
    try:
        await runtime.run_user_message(request("我身高179"))
        first = await MemoryRepository(database).active("user-1", key=MemoryKey.HEIGHT)
        await runtime.run_user_message(request("我身高179"))
        repeated = await MemoryRepository(database).active("user-1", key=MemoryKey.HEIGHT)
        updated = await runtime.run_user_message(
            request("我刚量了一下，身高应该是178")
        )
        second = await MemoryRepository(database).active("user-1", key=MemoryKey.HEIGHT)
        result = await runtime.run_user_message(request("帮我保存我的身高"))

        assert len(first) == 1
        assert first[0].value == {"millimeters": 1790}
        assert repeated[0].id == first[0].id
        assert len(second) == 1
        assert second[0].value == {"millimeters": 1780}
        assert second[0].supersedes_id == first[0].id
        assert updated.final_text == "刚量的是178cm，我已经从179cm更新好了。"
        update_context = next(
            message.content or ""
            for message in conversation.requests[2].messages
            if "权威用户事实" in (message.content or "")
        )
        assert '"action":"updated"' in update_context
        assert '"previous_value":{"millimeters":1790}' in update_context
        assert '"current_value":{"millimeters":1780}' in update_context
        update_items = await HarnessStateRepository(database).list_items(updated.turn_id)
        ingestion_trace = next(
            item for item in update_items if item.item_type is ItemType.MEMORY_INGESTION
        )
        assert ingestion_trace.payload["changes"] == [
            {
                "action": "updated",
                "key": "profile.height",
                "previous_value": {"millimeters": 1790},
                "current_value": {"millimeters": 1780},
            }
        ]
        assert result.final_text == "已经记着了，你身高178cm。"
        latest_context = next(
            message.content or ""
            for message in conversation.requests[-1].messages
            if "权威用户事实" in (message.content or "")
        )
        assert '"key":"profile.height"' in latest_context
        assert '"millimeters":1780' in latest_context
    finally:
        await ingestion.close()
        await conversation.close()
        await database.close()


async def test_ingestion_backfills_recent_user_evidence_outside_working_memory(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path, "memory-backfill.sqlite3")
    ingestion = HeightIngestionGateway((179,))
    conversation = ScriptedModelGateway((final("已经记好了，你身高179cm。"),))
    runtime = build_agent_runtime(
        database=database,
        model=conversation,
        memory_ingestion_model=ingestion,
        definition=definition("test-memory-backfill"),
        clock=lambda: NOW,
    )
    await AgentVersionRepository(database).register(runtime.manifest)
    state = HarnessStateRepository(database)
    try:
        original = await state.start_turn_with_items(
            user_id="user-1",
            agent_version_id=runtime.manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            items=(
                NewTurnItem(
                    item_type=ItemType.USER_MESSAGE,
                    status=ItemStatus.COMPLETED,
                    payload={"text": "我身高179"},
                ),
                NewTurnItem(
                    item_type=ItemType.AGENT_MESSAGE,
                    status=ItemStatus.COMPLETED,
                    payload={"text": "知道了。"},
                ),
            ),
        )
        await state.transition_turn(
            turn_id=original.turn.id,
            target=TurnStatus.COMPLETED,
            expected=TurnStatus.RUNNING,
        )
        for index in range(4):
            filler = await state.start_turn_with_items(
                user_id="user-1",
                agent_version_id=runtime.manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                items=(
                    NewTurnItem(
                        item_type=ItemType.USER_MESSAGE,
                        status=ItemStatus.COMPLETED,
                        payload={"text": f"普通聊天{index}"},
                    ),
                    NewTurnItem(
                        item_type=ItemType.AGENT_MESSAGE,
                        status=ItemStatus.COMPLETED,
                        payload={"text": "好的。"},
                    ),
                ),
            )
            await state.transition_turn(
                turn_id=filler.turn.id,
                target=TurnStatus.COMPLETED,
                expected=TurnStatus.RUNNING,
            )

        result = await runtime.run_user_message(request("帮我保存我的身高"))
        memories = await MemoryRepository(database).active(
            "user-1", key=MemoryKey.HEIGHT
        )

        assert result.final_text == "已经记好了，你身高179cm。"
        assert len(memories) == 1
        assert memories[0].value == {"millimeters": 1790}
        assert memories[0].evidence_item_id == original.items[0].id
        initial_reply_context = next(
            message.content or ""
            for message in conversation.requests[0].messages
            if "近期对话工作记忆" in (message.content or "")
        )
        assert "我身高179" not in initial_reply_context
    finally:
        await ingestion.close()
        await conversation.close()
        await database.close()
