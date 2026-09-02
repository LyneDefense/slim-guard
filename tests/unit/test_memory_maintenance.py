from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from slim_guard.db.models import (
    AgentItemRedactionRecord,
    MemoryHandoffRecord,
    SlimGuardUser,
    ToolExecutionRecord,
    UserMemoryFactRecord,
)
from slim_guard.db.session import Database
from slim_guard.domain.weight.contracts import (
    WeightMeasurementCommand,
    WeightMeasurementCondition,
    WeightUnit,
)
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository, NewTurnItem
from slim_guard.memory.contracts import (
    MemoryFactInput,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryWriteCommand,
)
from slim_guard.memory.handoff import HandoffRepository, HandoffUpsertCommand
from slim_guard.memory.lifecycle import MemoryLifecycleRepository
from slim_guard.memory.repository import MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository
from slim_guard.services.memory_maintenance import MemoryMaintenanceService
from slim_guard.tools.contracts import ToolResult
from slim_guard.tools.execution_repository import ToolExecutionRepository


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


async def test_memory_maintenance_scrubs_bodies_and_preserves_audit_and_domain_data(
    tmp_path: Path,
) -> None:
    recorded_at = datetime.now(UTC)
    maintenance_at = recorded_at + timedelta(days=31)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'privacy.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=recorded_at,
                last_seen_at=recorded_at,
            )
        )
    active_manifest = manifest()
    await AgentVersionRepository(database).register(active_manifest)
    state = HarnessStateRepository(database)
    started = await state.start_turn_with_items(
        user_id="user-1",
        agent_version_id=active_manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
        items=(
            NewTurnItem(
                item_type=ItemType.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={
                    "text": "以后叫我阿杰，今天77.6kg，下次继续",
                    "source_message_id": "private-message-id",
                    "channel_id": "default",
                },
            ),
            NewTurnItem(
                item_type=ItemType.CONTEXT_SNAPSHOT,
                status=ItemStatus.COMPLETED,
                payload={"request": {"messages": [{"content": "秘密上下文"}]}},
            ),
            NewTurnItem(
                item_type=ItemType.MODEL_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"message": {"role": "assistant", "content": "模型草稿"}},
            ),
            NewTurnItem(
                item_type=ItemType.TOOL_CALL,
                status=ItemStatus.COMPLETED,
                payload={
                    "tool_call_id": "call-memory",
                    "tool_name": "set_coaching_profile",
                    "arguments": {"preferred_name": "阿杰"},
                },
            ),
            NewTurnItem(
                item_type=ItemType.TOOL_RESULT,
                status=ItemStatus.COMPLETED,
                payload={"execution": {"result": {"output": {"name": "阿杰"}}}},
            ),
            NewTurnItem(
                item_type=ItemType.MEMORY_INGESTION,
                status=ItemStatus.COMPLETED,
                payload={
                    "changed_count": 1,
                    "changes": [
                        {
                            "action": "created",
                            "key": "identity.preferred_name",
                            "previous_value": None,
                            "current_value": {"name": "阿杰"},
                        }
                    ],
                },
            ),
            NewTurnItem(
                item_type=ItemType.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"text": "已记录阿杰的77.6kg"},
            ),
        ),
    )
    source_item = started.items[0]
    await state.transition_turn(
        turn_id=started.turn.id,
        target=TurnStatus.COMPLETED,
        expected=TurnStatus.RUNNING,
    )
    memories = MemoryRepository(database, clock=lambda: recorded_at)
    written = await memories.write(
        MemoryWriteCommand(
            user_id="user-1",
            facts=(
                MemoryFactInput(
                    key=MemoryKey.PREFERRED_NAME,
                    value={"name": "阿杰"},
                ),
            ),
            evidence_excerpt="以后叫我阿杰",
            operation_id="memory-write",
            source_turn_id=started.turn.id,
            source_item_id=source_item.id,
            source_tool_call_id="call-memory",
        )
    )
    await memories.revoke(
        MemoryRevokeCommand(
            user_id="user-1",
            memory_id=written.facts[0].id,
            operation_id="memory-revoke",
            source_turn_id=started.turn.id,
            source_item_id=source_item.id,
            source_tool_call_id="call-revoke",
        )
    )
    weights = WeightRepository(database)
    await weights.record(
        WeightMeasurementCommand(
            user_id="user-1",
            value=Decimal("77.6"),
            unit=WeightUnit.KG,
            measured_at=recorded_at,
            condition=WeightMeasurementCondition.UNSPECIFIED,
            idempotency_key="weight-operation",
            source_turn_id=started.turn.id,
            source_item_id=source_item.id,
            source_tool_call_id="call-weight",
        )
    )
    handoff = await HandoffRepository(
        database,
        ttl=timedelta(days=14),
        clock=lambda: recorded_at,
    ).upsert(
        HandoffUpsertCommand(
            user_id="user-1",
            thread_id=started.thread.id,
            objective="继续计划",
            unresolved=("完成计划",),
            evidence_excerpt="下次继续",
            operation_id="handoff-operation",
            source_turn_id=started.turn.id,
            source_item_id=source_item.id,
            source_tool_call_id="call-handoff",
        )
    )
    executions = ToolExecutionRepository(database)
    original_arguments = {
        "preferred_name": "阿杰",
        "evidence_excerpt": "以后叫我阿杰",
    }
    original_result = ToolResult.success(
        output={"memory": {"name": "阿杰"}},
        source_ids=(written.facts[0].id,),
    )
    claim = await executions.claim(
        idempotency_key="tool-operation",
        turn_id=started.turn.id,
        tool_call_id="call-ledger",
        tool_name="set_coaching_profile",
        tool_version="v4",
        canonical_arguments=original_arguments,
    )
    assert claim.created is True
    await executions.complete(
        idempotency_key="tool-operation",
        result=original_result,
    )
    service = MemoryMaintenanceService(
        lifecycle=MemoryLifecycleRepository(database),
        transcript_retention=timedelta(days=30),
        revoked_value_retention=timedelta(days=30),
        interval_seconds=60,
    )
    try:
        result = await service.run_once(now=maintenance_at)
        items = await state.list_items(started.turn.id)
        trend = await weights.recent_trend("user-1")
        replay = await executions.claim(
            idempotency_key="tool-operation",
            turn_id=started.turn.id,
            tool_call_id="call-ledger",
            tool_name="set_coaching_profile",
            tool_version="v4",
            canonical_arguments=original_arguments,
        )
        repeated_completion = await executions.complete(
            idempotency_key="tool-operation",
            result=original_result,
        )
        second = await service.run_once(now=maintenance_at)

        assert result.transcript.item_count == 7
        assert result.transcript.tool_execution_count == 1
        assert result.revoked_value_count == 1
        assert result.expired_handoff_count == 1
        assert all(item.payload.get("redacted") is True for item in items)
        assert all(
            "阿杰" not in str(item.payload) and "77.6" not in str(item.payload)
            for item in items
        )
        assert "source_message_id_sha256" in items[0].payload
        assert await ConversationWindowRepository(database).recent("user-1") == ()
        assert len(trend.records) == 1
        assert trend.records[0].weight_grams == 77_600
        assert replay.created is False
        assert replay.execution.result is not None
        assert replay.execution.result.output["_redacted"] is True
        assert repeated_completion.result is not None
        assert repeated_completion.result.output["_redacted"] is True
        assert second.transcript.item_count == 0
        assert second.transcript.tool_execution_count == 0
        assert second.revoked_value_count == 0

        async with database.session() as session:
            fact = await session.get(UserMemoryFactRecord, written.facts[0].id)
            handoff_row = await session.get(MemoryHandoffRecord, handoff.id)
            redactions = tuple(await session.scalars(select(AgentItemRedactionRecord)))
            execution = await session.get(ToolExecutionRecord, "tool-operation")
        assert fact is not None
        assert fact.value_json is None
        assert fact.value_hash == written.facts[0].value_hash
        assert handoff_row is not None and handoff_row.status == "expired"
        assert len(redactions) == 7
        assert execution is not None
        assert '"_redacted":true' in execution.canonical_arguments_json
    finally:
        await database.close()
