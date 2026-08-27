from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.errors import (
    InvalidPendingActionTransition,
    PendingActionCollision,
    PendingActionStateConflict,
)
from slim_guard.harness.events import PendingActionStatus, PendingActionType, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolExecutionMode


def manifest() -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={},
        system_prompt_version="test-v1",
        system_prompt="You are SlimGuard.",
        tool_versions={"record_weight": "v1"},
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test-revision",
    )


async def prepare_repository(
    tmp_path,
) -> tuple[Database, PendingActionRepository, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pending-actions.sqlite3'}")
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
    return database, PendingActionRepository(database), turn.thread_id, turn.id


async def create_action(
    repository: PendingActionRepository,
    *,
    thread_id: str,
    turn_id: str,
    expires_at: datetime,
    weight_kg: float = 77.6,
):
    return await repository.create(
        thread_id=thread_id,
        turn_id=turn_id,
        source_item_id=None,
        execution_key="tool-execution-1",
        tool_call_id="call-1",
        tool_name="record_weight",
        tool_version="v1",
        canonical_arguments={"weight_kg": weight_kg},
        execution_mode=ToolExecutionMode.EVALUATION,
        isolated_write_environment=True,
        action_type=PendingActionType.USER_CONFIRMATION,
        reason="Please confirm the weight before saving it.",
        expires_at=expires_at,
    )


async def test_create_is_idempotent_and_freezes_the_command(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    expiry = datetime.now(UTC) + timedelta(minutes=10)
    try:
        first = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=expiry,
        )
        duplicate = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=expiry,
        )

        assert first.created is True
        assert duplicate.created is False
        assert duplicate.action == first.action
        assert first.action.canonical_arguments == {"weight_kg": 77.6}
        assert first.action.status is PendingActionStatus.PENDING
    finally:
        await database.close()


async def test_same_execution_cannot_change_the_frozen_command(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    expiry = datetime.now(UTC) + timedelta(minutes=10)
    try:
        await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=expiry,
        )

        with pytest.raises(PendingActionCollision, match="identity collision"):
            await create_action(
                repository,
                thread_id=thread_id,
                turn_id=turn_id,
                expires_at=expiry,
                weight_kg=76.8,
            )
    finally:
        await database.close()


async def test_approval_and_consumption_are_idempotent(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        created = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=now + timedelta(minutes=10),
        )
        approved = await repository.resolve(
            action_id=created.action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            resolved_at=now,
        )
        repeated = await repository.resolve(
            action_id=created.action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            resolved_at=now + timedelta(seconds=1),
        )
        consumed = await repository.consume(
            action_id=created.action.id,
            consumed_at=now + timedelta(seconds=2),
        )
        consumed_again = await repository.consume(
            action_id=created.action.id,
            consumed_at=now + timedelta(seconds=3),
        )
        confirmed_again = await repository.resolve(
            action_id=created.action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            resolved_at=now + timedelta(seconds=4),
        )

        assert approved.status is PendingActionStatus.APPROVED
        assert repeated == approved
        assert consumed.status is PendingActionStatus.CONSUMED
        assert consumed.consumed_at == now + timedelta(seconds=2)
        assert consumed_again == consumed
        assert confirmed_again == consumed
    finally:
        await database.close()


async def test_resolution_by_a_different_actor_conflicts(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        created = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=now + timedelta(minutes=10),
        )
        await repository.resolve(
            action_id=created.action.id,
            resolution=PendingActionStatus.REJECTED,
            resolved_by="user-1",
            resolved_at=now,
        )

        with pytest.raises(PendingActionStateConflict, match="another actor"):
            await repository.resolve(
                action_id=created.action.id,
                resolution=PendingActionStatus.REJECTED,
                resolved_by="user-2",
                resolved_at=now,
            )
    finally:
        await database.close()


async def test_expired_action_cannot_be_approved(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        created = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=now,
        )
        expired = await repository.resolve(
            action_id=created.action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            resolved_at=now + timedelta(seconds=1),
        )

        assert expired.status is PendingActionStatus.EXPIRED
        with pytest.raises(InvalidPendingActionTransition, match="from expired"):
            await repository.resolve(
                action_id=created.action.id,
                resolution=PendingActionStatus.APPROVED,
                resolved_by="user-1",
                resolved_at=now + timedelta(seconds=2),
            )
    finally:
        await database.close()


async def test_list_open_and_expire_due_exclude_expired_actions(tmp_path) -> None:
    database, repository, thread_id, turn_id = await prepare_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        created = await create_action(
            repository,
            thread_id=thread_id,
            turn_id=turn_id,
            expires_at=now + timedelta(minutes=1),
        )
        assert [item.id for item in await repository.list_open(thread_id=thread_id, at=now)] == [
            created.action.id
        ]

        expired_count = await repository.expire_due(at=now + timedelta(minutes=2))
        assert expired_count == 1
        assert await repository.list_open(
            thread_id=thread_id,
            at=now + timedelta(minutes=2),
        ) == []
        loaded = await repository.get(created.action.id)
        assert loaded is not None
        assert loaded.status is PendingActionStatus.EXPIRED
    finally:
        await database.close()
