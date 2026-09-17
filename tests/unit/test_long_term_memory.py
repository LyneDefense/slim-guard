from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.errors import MemoryEvidenceMismatch
from slim_guard.memory.long_term import (
    LongTermMemoryCandidate,
    LongTermMemoryDurability,
    LongTermMemoryOperation,
    LongTermMemoryRepository,
    LongTermMemoryWriteAction,
)
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


async def _turn(database: Database, text: str):
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
    return await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text=text),),
        )
    )


async def _database(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'long-term-memory.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    return database


async def test_open_text_memory_is_evidence_bound_idempotent_and_supersedable(
    tmp_path,
) -> None:
    database = await _database(tmp_path)
    initialized = await _turn(
        database,
        "周日一般会和家里人聚餐，不过以后固定改到周六。",
    )
    assert initialized.source_item_id is not None
    repository = LongTermMemoryRepository(database, clock=lambda: NOW)
    first = LongTermMemoryCandidate(
        content_text="用户周日通常和家人聚餐。",
        category="social_routine",
        evidence_ref=initialized.source_item_id,
        evidence_excerpt="周日一般会和家里人聚餐",
    )
    try:
        created = await repository.apply(
            user_id="user-1",
            source_turn_id=initialized.turn.id,
            source_item_id=initialized.source_item_id,
            operation_prefix="extract-1",
            candidates=(first,),
        )
        replayed = await repository.apply(
            user_id="user-1",
            source_turn_id=initialized.turn.id,
            source_item_id=initialized.source_item_id,
            operation_prefix="extract-1",
            candidates=(first,),
        )
        assert created[0].action is LongTermMemoryWriteAction.CREATED
        assert replayed[0].action is LongTermMemoryWriteAction.UNCHANGED
        assert created[0].memory is not None

        replacement = LongTermMemoryCandidate(
            content_text="用户以后固定在周六和家人聚餐。",
            category="other",
            evidence_ref=initialized.source_item_id,
            evidence_excerpt="以后固定改到周六",
            operation=LongTermMemoryOperation.SUPERSEDE,
            supersedes_memory_id=created[0].memory.id,
        )
        changed = await repository.apply(
            user_id="user-1",
            source_turn_id=initialized.turn.id,
            source_item_id=initialized.source_item_id,
            operation_prefix="extract-2",
            candidates=(replacement,),
        )
        active = await repository.active("user-1")

        assert changed[0].action is LongTermMemoryWriteAction.SUPERSEDED
        assert changed[0].previous_memory_id == created[0].memory.id
        assert [memory.content_text for memory in active] == ["用户以后固定在周六和家人聚餐。"]
        assert active[0].category == "other"
    finally:
        await database.close()


async def test_long_term_memory_rejects_unquoted_evidence(tmp_path) -> None:
    database = await _database(tmp_path)
    initialized = await _turn(database, "晚上比白天更难控制饮食。")
    assert initialized.source_item_id is not None
    repository = LongTermMemoryRepository(database, clock=lambda: NOW)
    candidate = LongTermMemoryCandidate(
        content_text="用户晚上更难控制饮食。",
        category="other",
        evidence_ref=initialized.source_item_id,
        evidence_excerpt="模型自己推断的句子",
    )
    try:
        with pytest.raises(MemoryEvidenceMismatch):
            await repository.apply(
                user_id="user-1",
                source_turn_id=initialized.turn.id,
                source_item_id=initialized.source_item_id,
                operation_prefix="bad-evidence",
                candidates=(candidate,),
            )
    finally:
        await database.close()


def test_temporary_memory_requires_timezone_aware_expiry() -> None:
    with pytest.raises(ValueError, match="requires expires_at"):
        LongTermMemoryCandidate(
            content_text="用户下个月出差。",
            category="travel",
            durability=LongTermMemoryDurability.TEMPORARY,
            evidence_ref="item-1",
            evidence_excerpt="下个月出差",
        )
    candidate = LongTermMemoryCandidate(
        content_text="用户下个月出差。",
        category="travel",
        durability=LongTermMemoryDurability.TEMPORARY,
        expires_at=NOW + timedelta(days=45),
        evidence_ref="item-1",
        evidence_excerpt="下个月出差",
    )
    assert candidate.category == "travel"
