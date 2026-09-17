from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.db.models import MemoryExtractionJobRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository, NewTurnItem
from slim_guard.memory.contracts import MemoryKey
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


def final(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


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


async def test_ordinary_ingestion_is_queued_after_reply_and_does_not_write_profile(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path, "post-turn-memory.sqlite3")
    conversation = ScriptedModelGateway((final("收到。"),))
    # A distinct gateway enables the extraction scheduler, but must not be called inline.
    extractor = ScriptedModelGateway(())
    runtime = build_agent_runtime(
        database=database,
        model=conversation,
        memory_extraction_model=extractor,
        definition=definition("test-post-turn-memory"),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(request("我身高179"))
        structured = await MemoryRepository(database).active("user-1", key=MemoryKey.HEIGHT)
        async with database.session() as session:
            jobs = tuple(await session.scalars(select(MemoryExtractionJobRecord)))

        assert result.final_text == "收到。"
        assert structured == ()
        assert extractor.requests == []
        assert len(jobs) == 1
        assert jobs[0].turn_id == result.turn_id
        assert jobs[0].status == "queued"
    finally:
        await extractor.close()
        await conversation.close()
        await database.close()


async def test_post_turn_extraction_does_not_backfill_unrelated_historical_messages(
    tmp_path: Path,
) -> None:
    database = await prepare_database(tmp_path, "no-memory-backfill.sqlite3")
    conversation = ScriptedModelGateway((final("请把需要保存的资料再告诉我一次。"),))
    extractor = ScriptedModelGateway(())
    runtime = build_agent_runtime(
        database=database,
        model=conversation,
        memory_extraction_model=extractor,
        definition=definition("test-no-memory-backfill"),
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

        result = await runtime.run_user_message(request("帮我保存我的身高"))
        structured = await MemoryRepository(database).active("user-1", key=MemoryKey.HEIGHT)
        async with database.session() as session:
            jobs = tuple(await session.scalars(select(MemoryExtractionJobRecord)))

        assert result.final_text == "请把需要保存的资料再告诉我一次。"
        assert structured == ()
        assert len(jobs) == 1
        assert jobs[0].turn_id == result.turn_id
        assert jobs[0].turn_id != original.turn.id
    finally:
        await extractor.close()
        await conversation.close()
        await database.close()
