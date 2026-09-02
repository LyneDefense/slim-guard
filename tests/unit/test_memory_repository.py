from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from slim_guard.db.models import (
    SlimGuardUser,
    UserMemoryEventRecord,
    UserMemoryFactRecord,
)
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemStatus, ItemType, TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.contracts import (
    MemoryFactInput,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryStatus,
    MemoryWriteCommand,
)
from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemorySourceMismatch,
    MemoryStaleEvidence,
)
from slim_guard.memory.registry import MemorySchemaRegistry
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def test_memory_registry_rejects_unsafe_goal_ranges_and_review_periods() -> None:
    registry = MemorySchemaRegistry()

    with pytest.raises(ValidationError):
        registry.canonicalize(
            MemoryKey.TARGET_WEIGHT,
            {"grams": 5_000, "target_date": None},
        )
    assert registry.canonicalize(
        MemoryKey.HEIGHT,
        {"millimeters": 1790},
    ).value == {"millimeters": 1790}
    with pytest.raises(ValidationError):
        registry.canonicalize(MemoryKey.HEIGHT, {"millimeters": 3000})
    assert registry.canonicalize(
        MemoryKey.TARGET_BODY_FAT,
        {"basis_points": 2400},
    ).value == {"basis_points": 2400}
    with pytest.raises(ValidationError):
        registry.canonicalize(
            MemoryKey.BEHAVIOR_GOAL,
            {"kind": "weekly_exercise_sessions", "target": 20, "period": "week"},
        )
    with pytest.raises(ValueError, match="between 30 and 730"):
        MemorySchemaRegistry(health_review_days=7)


def manifest() -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={},
        system_prompt_version="test-v1",
        system_prompt="test",
        context_policy_version="test-v1",
        memory_policy_version="profile-goal-constraint-v2",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test",
    )


async def prepare(tmp_path) -> tuple[Database, MemoryRepository, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory.sqlite3'}")
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
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=active_manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(
                TurnInput.user_message(
                    text="以后叫我阿杰，回复简短点，我不喜欢香菜，也避开西芹"
                ),
            ),
        )
    )
    assert initialized.source_item_id is not None
    return database, MemoryRepository(database), initialized.turn.id, initialized.source_item_id


def write_command(
    *,
    turn_id: str,
    item_id: str,
    operation_id: str,
    facts: tuple[MemoryFactInput, ...],
    evidence: str,
    user_id: str = "user-1",
    evidence_item_id: str | None = None,
) -> MemoryWriteCommand:
    return MemoryWriteCommand(
        user_id=user_id,
        facts=facts,
        evidence_excerpt=evidence,
        operation_id=operation_id,
        source_turn_id=turn_id,
        source_item_id=item_id,
        evidence_item_id=evidence_item_id,
        source_tool_call_id=f"call-{operation_id}",
    )


async def test_memory_write_is_versioned_idempotent_and_user_scoped(tmp_path) -> None:
    database, repository, turn_id, item_id = await prepare(tmp_path)
    first_command = write_command(
        turn_id=turn_id,
        item_id=item_id,
        operation_id="operation-1",
        evidence="以后叫我阿杰，回复简短点",
        facts=(
            MemoryFactInput(key=MemoryKey.PREFERRED_NAME, value={"name": "阿杰"}),
            MemoryFactInput(
                key=MemoryKey.RESPONSE_STYLE,
                value={"style": "concise"},
            ),
        ),
    )
    try:
        first = await repository.write(first_command)
        replay = await repository.write(first_command)
        replacement = await repository.write(
            write_command(
                turn_id=turn_id,
                item_id=item_id,
                operation_id="operation-2",
                evidence="叫我阿杰",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.PREFERRED_NAME,
                        value={"name": "杰哥"},
                    ),
                ),
            )
        )
        active = await repository.active("user-1")

        assert first.created_count == 2
        assert replay.created_count == 0
        assert {fact.id for fact in replay.facts} == {fact.id for fact in first.facts}
        assert replacement.created_count == 1
        assert {fact.key for fact in active} == {
            MemoryKey.PREFERRED_NAME,
            MemoryKey.RESPONSE_STYLE,
        }
        preferred = next(fact for fact in active if fact.key is MemoryKey.PREFERRED_NAME)
        assert preferred.value == {"name": "杰哥"}
        assert preferred.supersedes_id == next(
            fact.id for fact in first.facts if fact.key is MemoryKey.PREFERRED_NAME
        )
        assert await repository.active("user-2") == ()

        async with database.session() as session:
            rows = tuple(await session.scalars(select(UserMemoryFactRecord)))
            events = tuple(await session.scalars(select(UserMemoryEventRecord)))
        assert sum(row.status == MemoryStatus.ACTIVE.value for row in rows) == 2
        assert sum(row.status == MemoryStatus.SUPERSEDED.value for row in rows) == 1
        assert [event.event_type for event in events].count("created") == 3
        assert [event.event_type for event in events].count("superseded") == 1
    finally:
        await database.close()


async def test_set_preferences_coexist_by_entity_and_replace_same_entity(tmp_path) -> None:
    database, repository, turn_id, item_id = await prepare(tmp_path)
    try:
        cilantro = await repository.write(
            write_command(
                turn_id=turn_id,
                item_id=item_id,
                operation_id="food-1",
                evidence="避开西芹",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.FOOD_PREFERENCE,
                        value={"item": "香菜", "stance": "dislike"},
                    ),
                ),
            )
        )
        await repository.write(
            write_command(
                turn_id=turn_id,
                item_id=item_id,
                operation_id="food-2",
                evidence="不喜欢香菜",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.FOOD_PREFERENCE,
                        value={"item": "西芹", "stance": "avoid"},
                    ),
                ),
            )
        )
        changed = await repository.write(
            write_command(
                turn_id=turn_id,
                item_id=item_id,
                operation_id="food-3",
                evidence="不喜欢香菜",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.FOOD_PREFERENCE,
                        value={"item": "香菜", "stance": "like"},
                    ),
                ),
            )
        )
        active = await repository.active("user-1", key=MemoryKey.FOOD_PREFERENCE)

        assert len(active) == 2
        assert {fact.value["item"] for fact in active} == {"香菜", "西芹"}
        assert changed.facts[0].supersedes_id == cilantro.facts[0].id
    finally:
        await database.close()


async def test_memory_requires_current_exact_user_evidence(tmp_path) -> None:
    database, repository, turn_id, item_id = await prepare(tmp_path)
    try:
        with pytest.raises(MemoryEvidenceMismatch):
            await repository.write(
                write_command(
                    turn_id=turn_id,
                    item_id=item_id,
                    operation_id="bad-evidence",
                    evidence="用户没有说过这句话",
                    facts=(
                        MemoryFactInput(
                            key=MemoryKey.PREFERRED_NAME,
                            value={"name": "阿杰"},
                        ),
                    ),
                )
            )
        with pytest.raises(MemorySourceMismatch):
            await repository.write(
                write_command(
                    turn_id=turn_id,
                    item_id=item_id,
                    operation_id="wrong-user",
                    evidence="叫我阿杰",
                    user_id="user-2",
                    facts=(
                        MemoryFactInput(
                            key=MemoryKey.PREFERRED_NAME,
                            value={"name": "阿杰"},
                        ),
                    ),
                )
            )
    finally:
        await database.close()


async def test_memory_accepts_same_user_historical_evidence_without_repetition(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'historical-memory.sqlite3'}")
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
    initializer = TurnInitializer(state)

    async def user_message(user_id: str, text: str):
        initialized = await initializer.initialize(
            TurnInitializationRequest(
                user_id=user_id,
                agent_version_id=active_manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=ToolExecutionMode.EVALUATION,
                inputs=(TurnInput.user_message(text=text),),
            )
        )
        assert initialized.source_item_id is not None
        return initialized

    historical = await user_message("user-1", "我身高179")
    current = await user_message("user-1", "帮我把身高保存了")
    other_user = await user_message("user-2", "我身高166")
    assistant_claim = await state.append_item(
        turn_id=historical.turn.id,
        item_type=ItemType.AGENT_MESSAGE,
        status=ItemStatus.COMPLETED,
        payload={"text": "你的身高是188"},
    )
    repository = MemoryRepository(database)
    try:
        result = await repository.write(
            write_command(
                turn_id=current.turn.id,
                item_id=current.source_item_id,
                operation_id="historical-height",
                evidence="我身高179",
                evidence_item_id=historical.source_item_id,
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.HEIGHT,
                        value={"millimeters": 1790},
                    ),
                ),
            )
        )

        assert result.created_count == 1
        assert result.facts[0].source_item_id == current.source_item_id
        assert result.facts[0].evidence_item_id == historical.source_item_id

        with pytest.raises(MemorySourceMismatch):
            await repository.write(
                write_command(
                    turn_id=current.turn.id,
                    item_id=current.source_item_id,
                    operation_id="cross-user-height",
                    evidence="我身高166",
                    evidence_item_id=other_user.source_item_id,
                    facts=(
                        MemoryFactInput(
                            key=MemoryKey.HEIGHT,
                            value={"millimeters": 1660},
                        ),
                    ),
                )
            )
        with pytest.raises(MemoryEvidenceMismatch):
            await repository.write(
                write_command(
                    turn_id=current.turn.id,
                    item_id=current.source_item_id,
                    operation_id="assistant-height",
                    evidence="你的身高是188",
                    evidence_item_id=assistant_claim.id,
                    facts=(
                        MemoryFactInput(
                            key=MemoryKey.HEIGHT,
                            value={"millimeters": 1880},
                        ),
                    ),
                )
            )
    finally:
        await database.close()


async def test_older_historical_evidence_cannot_replace_a_newer_memory(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'stale-memory.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    initializer = TurnInitializer(HarnessStateRepository(database))

    async def user_message(text: str):
        initialized = await initializer.initialize(
            TurnInitializationRequest(
                user_id="user-1",
                agent_version_id=active_manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=ToolExecutionMode.EVALUATION,
                inputs=(TurnInput.user_message(text=text),),
            )
        )
        assert initialized.source_item_id is not None
        return initialized

    old = await user_message("我身高179")
    newer = await user_message("我现在身高180")
    current = await user_message("还是保存上次那个")
    repository = MemoryRepository(database)
    try:
        first = await repository.write(
            write_command(
                turn_id=newer.turn.id,
                item_id=newer.source_item_id,
                operation_id="newer-height",
                evidence="我现在身高180",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.HEIGHT,
                        value={"millimeters": 1800},
                    ),
                ),
            )
        )
        same = await repository.write(
            write_command(
                turn_id=current.turn.id,
                item_id=current.source_item_id,
                operation_id="same-height",
                evidence="我现在身高180",
                evidence_item_id=newer.source_item_id,
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.HEIGHT,
                        value={"millimeters": 1800},
                    ),
                ),
            )
        )

        assert first.created_count == 1
        assert same.created_count == 0
        assert same.facts[0].id == first.facts[0].id

        with pytest.raises(MemoryStaleEvidence):
            await repository.write(
                write_command(
                    turn_id=current.turn.id,
                    item_id=current.source_item_id,
                    operation_id="stale-height",
                    evidence="我身高179",
                    evidence_item_id=old.source_item_id,
                    facts=(
                        MemoryFactInput(
                            key=MemoryKey.HEIGHT,
                            value={"millimeters": 1790},
                        ),
                    ),
                )
            )
    finally:
        await database.close()


async def test_concurrent_single_slot_writes_leave_exactly_one_active_fact(tmp_path) -> None:
    database, repository, turn_id, item_id = await prepare(tmp_path)
    commands = (
        write_command(
            turn_id=turn_id,
            item_id=item_id,
            operation_id="concurrent-1",
            evidence="叫我阿杰",
            facts=(
                MemoryFactInput(key=MemoryKey.PREFERRED_NAME, value={"name": "阿杰"}),
            ),
        ),
        write_command(
            turn_id=turn_id,
            item_id=item_id,
            operation_id="concurrent-2",
            evidence="叫我阿杰",
            facts=(
                MemoryFactInput(key=MemoryKey.PREFERRED_NAME, value={"name": "杰哥"}),
            ),
        ),
    )
    try:
        outcomes = await asyncio.gather(
            *(repository.write(command) for command in commands),
            return_exceptions=True,
        )
        active = await repository.active("user-1", key=MemoryKey.PREFERRED_NAME)

        assert any(not isinstance(outcome, Exception) for outcome in outcomes)
        assert all(
            not isinstance(outcome, Exception) or isinstance(outcome, MemoryCollision)
            for outcome in outcomes
        )
        assert len(active) == 1
        assert active[0].value["name"] in {"阿杰", "杰哥"}
    finally:
        await database.close()


async def test_revoke_is_immediate_idempotent_and_cannot_cross_users(tmp_path) -> None:
    database, repository, turn_id, item_id = await prepare(tmp_path)
    try:
        created = await repository.write(
            write_command(
                turn_id=turn_id,
                item_id=item_id,
                operation_id="name-create",
                evidence="叫我阿杰",
                facts=(
                    MemoryFactInput(
                        key=MemoryKey.PREFERRED_NAME,
                        value={"name": "阿杰"},
                    ),
                ),
            )
        )
        command = MemoryRevokeCommand(
            user_id="user-1",
            memory_id=created.facts[0].id,
            operation_id="forget-1",
            source_turn_id=turn_id,
            source_item_id=item_id,
            source_tool_call_id="call-forget",
        )
        revoked = await repository.revoke(command)
        duplicate = await repository.revoke(command)

        assert revoked.changed is True
        assert revoked.fact.status is MemoryStatus.REVOKED
        assert duplicate.changed is False
        assert await repository.active("user-1") == ()
        with pytest.raises(MemorySourceMismatch):
            await repository.revoke(command.model_copy(update={"user_id": "user-2"}))
    finally:
        await database.close()
