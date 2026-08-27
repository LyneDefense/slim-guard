from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemStatus, ItemType, ThreadStatus, TurnStatus, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository


def build_manifest() -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"thinking": {"type": "disabled"}},
        system_prompt_version="legacy-v1",
        system_prompt="You are SlimGuard.",
        context_policy_version="single-turn-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="legacy-v1",
        code_revision="test-revision",
    )


async def prepare_state(tmp_path) -> tuple[Database, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'harness-state.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    user = SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now)
    async with database.session() as session, session.begin():
        session.add(user)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    return database, user.id, manifest.version_id


async def test_get_or_create_thread_is_idempotent(tmp_path) -> None:
    database, user_id, _ = await prepare_state(tmp_path)
    repository = HarnessStateRepository(database)
    try:
        first = await repository.get_or_create_thread(user_id)
        second = await repository.get_or_create_thread(user_id)

        assert first == second
        assert first.user_id == user_id
        assert first.status is ThreadStatus.ACTIVE
    finally:
        await database.close()


async def test_turn_references_the_frozen_agent_version(tmp_path) -> None:
    database, user_id, agent_version_id = await prepare_state(tmp_path)
    repository = HarnessStateRepository(database)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    try:
        turn = await repository.start_turn(
            user_id=user_id,
            agent_version_id=agent_version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            deadline_at=deadline,
        )

        assert turn.agent_version_id == agent_version_id
        assert turn.trigger is TurnTrigger.USER_MESSAGE
        assert turn.status is TurnStatus.RUNNING
    finally:
        await database.close()


async def test_items_are_appended_in_turn_order(tmp_path) -> None:
    database, user_id, agent_version_id = await prepare_state(tmp_path)
    repository = HarnessStateRepository(database)
    try:
        turn = await repository.start_turn(
            user_id=user_id,
            agent_version_id=agent_version_id,
            trigger=TurnTrigger.USER_MESSAGE,
        )
        first = await repository.append_item(
            turn_id=turn.id,
            item_type=ItemType.USER_MESSAGE,
            status=ItemStatus.COMPLETED,
            payload={"text": "今天77.6kg"},
        )
        second = await repository.append_item(
            turn_id=turn.id,
            item_type=ItemType.MODEL_MESSAGE,
            status=ItemStatus.COMPLETED,
            payload={"text": "我来记录。"},
        )

        items = await repository.list_items(turn.id)

        assert first.sequence == 1
        assert second.sequence == 2
        assert [item.item_type for item in items] == [
            ItemType.USER_MESSAGE,
            ItemType.MODEL_MESSAGE,
        ]
        assert items[0].payload == {"text": "今天77.6kg"}
    finally:
        await database.close()
