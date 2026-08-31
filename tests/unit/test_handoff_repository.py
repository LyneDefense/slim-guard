from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.errors import MemoryEvidenceMismatch, MemoryNotFound
from slim_guard.memory.handoff import (
    HandoffRepository,
    HandoffResolveCommand,
    HandoffUpsertCommand,
)
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


async def prepare_source(database: Database, user_id: str) -> tuple[str, str, str]:
    active_manifest = AgentManifest.build(
        model_provider="test",
        text_model="test",
        vision_model="test",
        model_parameters={},
        system_prompt_version="test-v1",
        system_prompt="test",
        context_policy_version="test-v1",
        memory_policy_version="test-v1",
        compaction_policy_version="test-v1",
        safety_policy_version="test-v1",
        code_revision="test",
    )
    await AgentVersionRepository(database).register(active_manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id=user_id,
            agent_version_id=active_manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="下次继续A，也可以下次继续B，现在取消"),),
        )
    )
    assert initialized.source_item_id is not None
    return initialized.thread.id, initialized.turn.id, initialized.source_item_id


def command(
    *,
    user_id: str,
    thread_id: str,
    turn_id: str,
    item_id: str,
    operation_id: str,
    objective: str,
    evidence: str,
) -> HandoffUpsertCommand:
    return HandoffUpsertCommand(
        user_id=user_id,
        thread_id=thread_id,
        objective=objective,
        unresolved=(f"完成{objective}",),
        evidence_excerpt=evidence,
        operation_id=operation_id,
        source_turn_id=turn_id,
        source_item_id=item_id,
        source_tool_call_id=f"call-{operation_id}",
    )


async def test_handoff_replaces_replays_expires_and_resolves_per_user(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'handoff.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    thread_id, turn_id, item_id = await prepare_source(database, "user-1")
    _, other_turn, other_item = await prepare_source(database, "user-2")
    current = [NOW]
    repository = HandoffRepository(
        database,
        ttl=timedelta(days=14),
        clock=lambda: current[0],
    )
    first_command = command(
        user_id="user-1",
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        operation_id="operation-a",
        objective="事项A",
        evidence="下次继续A",
    )
    try:
        first = await repository.upsert(first_command)
        replay = await repository.upsert(first_command)
        second = await repository.upsert(
            command(
                user_id="user-1",
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                operation_id="operation-b",
                objective="事项B",
                evidence="下次继续B",
            )
        )

        assert replay.id == first.id
        assert (await repository.active("user-1")).id == second.id  # type: ignore[union-attr]
        assert await repository.active("user-2") is None
        with pytest.raises(MemoryNotFound):
            await repository.resolve(
                HandoffResolveCommand(
                    user_id="user-2",
                    handoff_id=second.id,
                    source_turn_id=other_turn,
                    source_item_id=other_item,
                )
            )

        resolved, changed = await repository.resolve(
            HandoffResolveCommand(
                user_id="user-1",
                handoff_id=second.id,
                source_turn_id=turn_id,
                source_item_id=item_id,
            )
        )
        assert changed is True
        assert resolved.status == "resolved"
        assert await repository.active("user-1") is None

        third = await repository.upsert(
            command(
                user_id="user-1",
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                operation_id="operation-c",
                objective="事项A",
                evidence="下次继续A",
            )
        )
        current[0] = NOW + timedelta(days=15)
        assert third.expires_at == NOW + timedelta(days=14)
        assert await repository.active("user-1") is None
    finally:
        await database.close()


async def test_handoff_requires_exact_current_message_evidence(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'evidence.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    thread_id, turn_id, item_id = await prepare_source(database, "user-1")
    repository = HandoffRepository(database, clock=lambda: NOW)
    try:
        with pytest.raises(MemoryEvidenceMismatch):
            await repository.upsert(
                command(
                    user_id="user-1",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_id=item_id,
                    operation_id="operation-invalid",
                    objective="事项C",
                    evidence="用户没说过这句话",
                )
            )
    finally:
        await database.close()
