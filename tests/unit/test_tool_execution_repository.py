from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolExecutionStatus, ToolResult
from slim_guard.tools.errors import ToolExecutionCollision, ToolExecutionStateConflict
from slim_guard.tools.execution_repository import ToolExecutionRepository


def manifest() -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"thinking": {"type": "disabled"}},
        system_prompt_version="test-v1",
        system_prompt="You are SlimGuard.",
        tool_versions={"record_weight": "v1"},
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test-revision",
    )


async def prepare_ledger(tmp_path) -> tuple[Database, ToolExecutionRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'tool-ledger.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    agent_manifest = manifest()
    await AgentVersionRepository(database).register(agent_manifest)
    turn = await HarnessStateRepository(database).start_turn(
        user_id="user-1",
        agent_version_id=agent_manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
    )
    return database, ToolExecutionRepository(database), turn.id


async def claim(
    repository: ToolExecutionRepository,
    turn_id: str,
    *,
    idempotency_key: str = "tool-stable-key",
    tool_call_id: str = "call-1",
    weight_kg: float = 77.6,
):
    return await repository.claim(
        idempotency_key=idempotency_key,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        tool_name="record_weight",
        tool_version="v1",
        canonical_arguments={"weight_kg": weight_kg},
    )


async def test_claim_is_idempotent_and_persists_canonical_arguments(tmp_path) -> None:
    database, repository, turn_id = await prepare_ledger(tmp_path)
    try:
        first = await claim(repository, turn_id)
        duplicate = await claim(repository, turn_id)

        assert first.created is True
        assert duplicate.created is False
        assert duplicate.execution == first.execution
        assert first.execution.status is ToolExecutionStatus.RUNNING
        assert first.execution.canonical_arguments == {"weight_kg": 77.6}
    finally:
        await database.close()


async def test_concurrent_claims_have_one_winner(tmp_path) -> None:
    database, repository, turn_id = await prepare_ledger(tmp_path)
    try:
        claims = await asyncio.gather(
            claim(repository, turn_id),
            claim(repository, turn_id),
        )

        assert sorted(item.created for item in claims) == [False, True]
        assert claims[0].execution.idempotency_key == claims[1].execution.idempotency_key
    finally:
        await database.close()


async def test_claim_detects_key_or_tool_call_identity_collisions(tmp_path) -> None:
    database, repository, turn_id = await prepare_ledger(tmp_path)
    try:
        await claim(repository, turn_id)

        with pytest.raises(ToolExecutionCollision, match="identity collision"):
            await claim(repository, turn_id, weight_kg=76.9)
        with pytest.raises(ToolExecutionCollision, match="identity collision"):
            await claim(repository, turn_id, idempotency_key="tool-different-key")
    finally:
        await database.close()


async def test_completion_is_persisted_and_same_completion_is_idempotent(tmp_path) -> None:
    database, repository, turn_id = await prepare_ledger(tmp_path)
    result = ToolResult.success(
        output={"weight_kg": 77.6, "recorded": True},
        source_ids=("weight-1",),
    )
    try:
        await claim(repository, turn_id)
        completed = await repository.complete(
            idempotency_key="tool-stable-key",
            result=result,
        )
        duplicate = await repository.complete(
            idempotency_key="tool-stable-key",
            result=result,
        )
        loaded = await repository.get("tool-stable-key")

        assert completed.status is ToolExecutionStatus.SUCCEEDED
        assert completed.completed_at is not None
        assert completed.result == result
        assert duplicate == completed
        assert loaded == completed
    finally:
        await database.close()


async def test_completion_rejects_a_different_terminal_result(tmp_path) -> None:
    database, repository, turn_id = await prepare_ledger(tmp_path)
    try:
        await claim(repository, turn_id)
        await repository.complete(
            idempotency_key="tool-stable-key",
            result=ToolResult.success(output={"recorded": True}),
        )

        with pytest.raises(ToolExecutionStateConflict, match="completed differently"):
            await repository.complete(
                idempotency_key="tool-stable-key",
                result=ToolResult.failed(code="failed_later", message="Different result"),
            )
    finally:
        await database.close()
