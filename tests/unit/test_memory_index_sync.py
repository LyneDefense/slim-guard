from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from slim_guard.db.models import MemoryIndexOutboxRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.contracts import (
    MemoryFactInput,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryWriteCommand,
)
from slim_guard.memory.engine import SemanticMemory
from slim_guard.memory.index_sync import MemoryIndexSyncRepository, MemoryIndexSyncService
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


class RecordingMemoryEngine:
    provider_name = "recording"

    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []
        self.deletes: list[tuple[str, str]] = []

    async def search(
        self, *, user_id: str, query: str, limit: int
    ) -> tuple[SemanticMemory, ...]:
        del user_id, query, limit
        return ()

    async def upsert_canonical(
        self,
        *,
        user_id: str,
        memory_id: str,
        value_hash: str,
        text: str,
        metadata: dict[str, object],
    ) -> None:
        self.upserts.append(
            {
                "user_id": user_id,
                "memory_id": memory_id,
                "value_hash": value_hash,
                "text": text,
                "metadata": metadata,
            }
        )

    async def delete_canonical(self, *, user_id: str, memory_id: str) -> None:
        self.deletes.append((user_id, memory_id))

    async def delete_user(self, *, user_id: str) -> None:
        self.deletes.append((user_id, "*"))

    async def close(self) -> None:
        return None


async def test_authoritative_write_queues_and_projects_to_semantic_engine(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-index.sqlite3'}")
    await database.create_schema()
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
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="我身高179"),),
        )
    )
    assert initialized.source_item_id is not None
    memories = MemoryRepository(database, clock=lambda: NOW, index_sync_enabled=True)
    engine = RecordingMemoryEngine()
    service = MemoryIndexSyncService(
        repository=MemoryIndexSyncRepository(database, clock=lambda: NOW),
        engine=engine,
    )
    command = MemoryWriteCommand(
        user_id="user-1",
        facts=(
            MemoryFactInput(key=MemoryKey.HEIGHT, value={"millimeters": 1790}),
        ),
        evidence_excerpt="我身高179",
        operation_id="height-179",
        source_turn_id=initialized.turn.id,
        source_item_id=initialized.source_item_id,
        source_tool_call_id="call-height-179",
    )
    try:
        written = await memories.write(command)
        replay = await memories.write(command)
        processed = await service.process_once()

        assert written.created_count == 1
        assert replay.created_count == 0
        assert processed == 1
        assert len(engine.upserts) == 1
        assert engine.upserts[0]["memory_id"] == written.facts[0].id
        assert "身高" in str(engine.upserts[0]["text"])
        async with database.session() as session:
            tasks = tuple(await session.scalars(select(MemoryIndexOutboxRecord)))
        assert len(tasks) == 1
        assert tasks[0].status == "completed"
        assert tasks[0].attempt_count == 1

        revoked = await memories.revoke(
            MemoryRevokeCommand(
                user_id="user-1",
                memory_id=written.facts[0].id,
                operation_id="forget-height",
                source_turn_id=initialized.turn.id,
                source_item_id=initialized.source_item_id,
                source_tool_call_id="call-forget-height",
            )
        )
        assert revoked.changed is True
        assert await service.process_once() == 1
        assert engine.deletes == [("user-1", written.facts[0].id)]
    finally:
        await database.close()
