from __future__ import annotations

from datetime import UTC, datetime

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.long_term import LongTermMemoryRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.long_term_memory import (
    ForgetLongTermMemoryArguments,
    ListLongTermMemoriesArguments,
    LongTermMemoryToolHandlers,
    RememberLongTermMemoryArguments,
)

NOW = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)


async def test_explicit_remember_list_and_forget_use_authoritative_text_store(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-tools.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    manifest = AgentManifest.build(
        model_provider="test",
        text_model="test",
        vision_model="test",
        model_parameters={},
        system_prompt_version="test",
        system_prompt="test",
        context_policy_version="test",
        memory_policy_version="test",
        compaction_policy_version="test",
        safety_policy_version="test",
        code_revision="test",
    )
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="请记住我周日通常和家人聚餐；现在请忘掉这件事。"),),
        )
    )
    assert initialized.source_item_id is not None
    repository = LongTermMemoryRepository(database, clock=lambda: NOW)
    handlers = LongTermMemoryToolHandlers(repository)
    base_context = ToolContext(
        thread_id=initialized.thread.id,
        turn_id=initialized.turn.id,
        tool_call_id="remember-call",
        user_id="user-1",
        agent_version_id=manifest.version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
        source_item_id=initialized.source_item_id,
        execution_idempotency_key="remember-operation",
    )
    try:
        remembered = await handlers.remember(
            base_context,
            RememberLongTermMemoryArguments(
                content_text="用户周日通常和家人聚餐。",
                category="social_routine",
                evidence_excerpt="请记住我周日通常和家人聚餐",
            ),
        )
        assert remembered.status is ToolResultStatus.SUCCEEDED
        memory_id = str(remembered.output["memory"]["memory_id"])

        listed = await handlers.list_memories(
            base_context.model_copy(
                update={
                    "tool_call_id": "list-call",
                    "execution_idempotency_key": "list-operation",
                }
            ),
            ListLongTermMemoriesArguments(),
        )
        assert listed.output["memories"] == [
            {
                "memory_id": memory_id,
                "content_text": "用户周日通常和家人聚餐。",
                "category": "social_routine",
                "durability": "long_term",
                "status": "active",
                "expires_at": None,
            }
        ]

        forgotten = await handlers.forget(
            base_context.model_copy(
                update={
                    "tool_call_id": "forget-call",
                    "execution_idempotency_key": "forget-operation",
                }
            ),
            ForgetLongTermMemoryArguments(
                memory_id=memory_id,
                evidence_excerpt="请忘掉这件事",
            ),
        )
        assert forgotten.status is ToolResultStatus.SUCCEEDED
        assert forgotten.output["changed"] is True
        assert await repository.active("user-1") == ()
    finally:
        await database.close()


async def test_explicit_memory_write_rejects_nonverbatim_evidence(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-evidence.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    manifest = AgentManifest.build(
        model_provider="test",
        text_model="test",
        vision_model="test",
        model_parameters={},
        system_prompt_version="test",
        system_prompt="test",
        context_policy_version="test",
        memory_policy_version="test",
        compaction_policy_version="test",
        safety_policy_version="test",
        code_revision="test",
    )
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="请记住我周日通常和家人聚餐"),),
        )
    )
    assert initialized.source_item_id is not None
    handlers = LongTermMemoryToolHandlers(LongTermMemoryRepository(database, clock=lambda: NOW))
    try:
        result = await handlers.remember(
            ToolContext(
                thread_id=initialized.thread.id,
                turn_id=initialized.turn.id,
                tool_call_id="remember-call",
                user_id="user-1",
                agent_version_id=manifest.version_id,
                execution_mode=ToolExecutionMode.EVALUATION,
                source_item_id=initialized.source_item_id,
                execution_idempotency_key="remember-operation",
            ),
            RememberLongTermMemoryArguments(
                content_text="用户周日通常和家人聚餐。",
                evidence_excerpt="模型改写后并不存在的证据",
            ),
        )
        assert result.status is ToolResultStatus.FAILED
        assert result.failure is not None
        assert result.failure.code == "long_term_memory_evidence_mismatch"
    finally:
        await database.close()
