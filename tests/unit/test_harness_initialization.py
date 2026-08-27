from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from slim_guard.db.models import AgentTurnRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolExecutionMode


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


async def prepare_initializer(
    tmp_path,
) -> tuple[Database, TurnInitializer, SlimGuardUser, AgentManifest]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'turn-initializer.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    user = SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now)
    async with database.session() as session, session.begin():
        session.add(user)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    initializer = TurnInitializer(HarnessStateRepository(database))
    return database, initializer, user, manifest


async def test_initializer_atomically_creates_turn_inputs_and_context(tmp_path) -> None:
    database, initializer, user, manifest = await prepare_initializer(tmp_path)
    occurred_at = datetime(2026, 8, 27, 7, 30, tzinfo=UTC)
    deadline = occurred_at + timedelta(seconds=30)
    try:
        initialized = await initializer.initialize(
            TurnInitializationRequest(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=ToolExecutionMode.LIVE,
                deadline_at=deadline,
                inputs=(
                    TurnInput.user_message(
                        text="  今天 77.6kg  ",
                        source_message_id="wecom-message-1",
                        channel_id="default",
                        occurred_at=occurred_at,
                    ),
                    TurnInput.image_attachment(
                        asset_id="asset-scale-1",
                        mime_type="IMAGE/JPEG",
                        source_message_id="wecom-message-1",
                        channel_id="default",
                        occurred_at=occurred_at,
                    ),
                ),
            )
        )

        assert initialized.thread.user_id == user.id
        assert initialized.turn.agent_version_id == manifest.version_id
        assert initialized.turn.status is TurnStatus.RUNNING
        assert initialized.turn.deadline_at == deadline
        assert initialized.context.thread_id == initialized.thread.id
        assert initialized.context.turn_id == initialized.turn.id
        assert initialized.context.deadline_at == deadline
        assert initialized.context.execution_mode is ToolExecutionMode.LIVE
        assert [item.item_type for item in initialized.input_items] == [
            ItemType.USER_MESSAGE,
            ItemType.IMAGE_ATTACHMENT,
        ]
        assert initialized.input_items[0].payload == {
            "channel_id": "default",
            "occurred_at": occurred_at.isoformat(),
            "source_message_id": "wecom-message-1",
            "text": "今天 77.6kg",
        }
        assert initialized.input_items[1].payload["asset_id"] == "asset-scale-1"
        assert initialized.input_items[1].payload["mime_type"] == "image/jpeg"
        assert initialized.source_item_id == initialized.input_items[0].id
    finally:
        await database.close()


async def test_scheduled_turn_can_start_without_external_inputs(tmp_path) -> None:
    database, initializer, user, manifest = await prepare_initializer(tmp_path)
    try:
        initialized = await initializer.initialize(
            TurnInitializationRequest(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                trigger=TurnTrigger.DAILY_REVIEW,
                execution_mode=ToolExecutionMode.SHADOW,
            )
        )

        assert initialized.input_items == ()
        assert initialized.source_item_id is None
        assert initialized.turn.trigger is TurnTrigger.DAILY_REVIEW
    finally:
        await database.close()


async def test_user_message_turn_requires_text_or_image(tmp_path) -> None:
    database, _, user, manifest = await prepare_initializer(tmp_path)
    try:
        with pytest.raises(ValidationError, match="requires text or an image"):
            TurnInitializationRequest(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=ToolExecutionMode.EVALUATION,
            )
    finally:
        await database.close()


async def test_unserializable_input_does_not_leave_partial_turn(tmp_path) -> None:
    database, initializer, user, manifest = await prepare_initializer(tmp_path)
    try:
        request = TurnInitializationRequest(
            user_id=user.id,
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(
                TurnInput(
                    item_type=ItemType.USER_MESSAGE,
                    payload={"text": "hello", "invalid": object()},
                ),
            ),
        )

        with pytest.raises(TypeError, match="not JSON serializable"):
            await initializer.initialize(request)

        async with database.session() as session:
            turn_count = await session.scalar(select(func.count(AgentTurnRecord.id)))
        assert turn_count == 0
    finally:
        await database.close()
