from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository, NewTurnItem
from slim_guard.memory.working import ConversationWindowRepository

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def manifest() -> AgentManifest:
    return AgentManifest.build(
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


async def add_completed_turn(
    state: HarnessStateRepository,
    *,
    user_id: str,
    agent_version_id: str,
    user_text: str,
    assistant_text: str,
) -> str:
    started = await state.start_turn_with_items(
        user_id=user_id,
        agent_version_id=agent_version_id,
        trigger=TurnTrigger.USER_MESSAGE,
        items=(
            NewTurnItem(
                item_type=ItemType.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"text": user_text},
            ),
            NewTurnItem(
                item_type=ItemType.CONTEXT_SNAPSHOT,
                status=ItemStatus.COMPLETED,
                payload={"secret": "must-not-leak"},
            ),
            NewTurnItem(
                item_type=ItemType.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"text": assistant_text},
            ),
        ),
    )
    await state.transition_turn(
        turn_id=started.turn.id,
        target=TurnStatus.COMPLETED,
        expected=TurnStatus.RUNNING,
    )
    return started.turn.id


async def test_recent_dialogue_is_user_isolated_visible_and_bounded(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'working.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    state = HarnessStateRepository(database)
    try:
        old_id = await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="最早的问题",
            assistant_text="最早的回答",
        )
        middle_id = await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="中间的问题",
            assistant_text="中间的回答",
        )
        newest_id = await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="刚才的问题",
            assistant_text="刚才的回答",
        )
        await add_completed_turn(
            state,
            user_id="user-2",
            agent_version_id=active_manifest.version_id,
            user_text="另一个用户的问题",
            assistant_text="另一个用户的回答",
        )

        recent = await ConversationWindowRepository(database).recent(
            "user-1",
            turn_limit=2,
            char_limit=100,
        )

        assert [turn.turn_id for turn in recent] == [middle_id, newest_id]
        assert old_id not in {turn.turn_id for turn in recent}
        assert [message.role for turn in recent for message in turn.messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        contents = [message.content for turn in recent for message in turn.messages]
        assert contents == ["中间的问题", "中间的回答", "刚才的问题", "刚才的回答"]
        assert [
            message.evidence_ref for turn in recent for message in turn.messages
        ][1::2] == [None, None]
        assert all(
            message.evidence_ref
            for turn in recent
            for message in turn.messages
            if message.role == "user"
        )
        assert all("must-not-leak" not in content for content in contents)
        assert all("另一个用户" not in content for content in contents)
    finally:
        await database.close()


async def test_recent_dialogue_keeps_the_newest_text_within_character_budget(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'budget.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    state = HarnessStateRepository(database)
    try:
        await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="问" * 80,
            assistant_text="答" * 80,
        )

        recent = await ConversationWindowRepository(database).recent(
            "user-1",
            turn_limit=3,
            char_limit=100,
        )

        messages = recent[0].messages
        assert sum(len(message.content) for message in messages) == 100
        assert messages[-1].content == "答" * 80
        assert messages[0].content == "问" * 20
    finally:
        await database.close()


async def test_recent_user_evidence_is_user_only_ordered_and_bounded(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'evidence.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    state = HarnessStateRepository(database)
    try:
        first_id = await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="我身高179",
            assistant_text="助手说身高188",
        )
        excluded_id = await add_completed_turn(
            state,
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            user_text="当前轮不应重复加载",
            assistant_text="好的",
        )
        await add_completed_turn(
            state,
            user_id="user-2",
            agent_version_id=active_manifest.version_id,
            user_text="我身高166",
            assistant_text="好的",
        )

        evidence = await ConversationWindowRepository(database).recent_user_evidence(
            "user-1",
            exclude_turn_id=excluded_id,
            limit=20,
            char_limit=6000,
        )
        items = await state.list_items(first_id)
        user_item = next(item for item in items if item.item_type is ItemType.USER_MESSAGE)

        assert [(item.content, item.evidence_ref) for item in evidence] == [
            ("我身高179", user_item.id)
        ]
        assert all("188" not in item.content for item in evidence)
        assert all("166" not in item.content for item in evidence)
    finally:
        await database.close()


async def test_recent_images_are_user_scoped_unexpired_and_include_observation(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'recent-images.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    assets = ImageAssetRepository(database)
    active = await assets.save(
        SaveImageAssetCommand(
            user_id="user-1",
            content=b"\x89PNG\r\n\x1a\nactive",
            declared_mime_type="image/png",
            channel_id="default",
            source_message_id="image-active",
            expires_at=NOW.replace(hour=9),
        )
    )
    await assets.save(
        SaveImageAssetCommand(
            user_id="user-1",
            content=b"\x89PNG\r\n\x1a\nexpired",
            declared_mime_type="image/png",
            channel_id="default",
            source_message_id="image-expired",
            expires_at=NOW.replace(hour=7),
        )
    )
    await assets.save(
        SaveImageAssetCommand(
            user_id="user-2",
            content=b"\x89PNG\r\n\x1a\nother-user",
            declared_mime_type="image/png",
            channel_id="default",
            source_message_id="image-other-user",
            expires_at=NOW.replace(hour=9),
        )
    )
    state = HarnessStateRepository(database)
    started = await state.start_turn_with_items(
        user_id="user-1",
        agent_version_id=active_manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
        items=(
            NewTurnItem(
                item_type=ItemType.IMAGE_ATTACHMENT,
                status=ItemStatus.COMPLETED,
                payload={"asset_id": active.asset.id, "mime_type": "image/png"},
            ),
            NewTurnItem(
                item_type=ItemType.TOOL_RESULT,
                status=ItemStatus.COMPLETED,
                payload={
                    "tool_name": "inspect_image",
                    "execution": {
                        "tool_name": "inspect_image",
                        "result": {
                            "status": "succeeded",
                            "output": {
                                "asset_id": active.asset.id,
                                "description": "一盘白米饭和蔬菜，部分食材不确定。",
                                "requires_user_confirmation": True,
                            },
                        },
                    },
                },
            ),
        ),
    )
    await state.transition_turn(
        turn_id=started.turn.id,
        target=TurnStatus.SUSPENDED,
        expected=TurnStatus.RUNNING,
    )
    try:
        recent = await ConversationWindowRepository(database).recent_images(
            "user-1",
            at=NOW,
            limit=3,
        )

        assert len(recent) == 1
        assert recent[0].asset_id == active.asset.id
        assert recent[0].mime_type == "image/png"
        assert recent[0].observation == "一盘白米饭和蔬菜，部分食材不确定。"
        assert recent[0].requires_user_confirmation is True
    finally:
        await database.close()
