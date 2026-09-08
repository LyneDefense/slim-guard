"""Synthetic model script; real runtime, tools and SQLite verify rollback durability.

This exercises rollout mechanics, not real-model semantics or a production rollback.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import (
    AgentInvocationRecord,
    AgentThreadRecord,
    SlimGuardUser,
    WeightRecord,
)
from slim_guard.db.session import Database
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemType, TurnStatus
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.termination import HarnessTermination
from slim_guard.orchestration.coordinator import direct_shadow_directive
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 8, 8, tzinfo=UTC)


def text_response(text: str) -> ModelResponse:
    return ModelResponse(message=ModelMessage(role=MessageRole.ASSISTANT, content=text))


def tool_response(call_id: str, tool: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(NormalizedToolCall(id=call_id, name=tool, arguments=arguments),),
        )
    )


async def test_on_to_off_after_restart_preserves_thread_records_and_harness_reply(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test-real-rollback.sqlite'}"
    database = Database(database_url)
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="test-rollback-user", first_seen_at=NOW, last_seen_at=NOW))

    baseline = "已记录今天的体重77.6kg。"
    adopted_text = "已记录今天的体重77.6kg！"
    legacy_after_rollback = "之前的77.6kg记录还在，这次没有新增记录。"
    on_model = ScriptedModelGateway(
        [
            tool_response("test-write-once", "record_weight", {"value": 77.6, "unit": "kg"}),
            text_response(baseline),
            text_response(direct_shadow_directive("确认本轮记录").model_dump_json()),
            text_response(
                json.dumps(
                    {
                        "text": adopted_text,
                        "used_block_ids": ["verified-harness-response"],
                        "style_profile_version": "slimguard_default_v1",
                    }
                )
            ),
            text_response('{"verdict":"pass"}'),
        ]
    )
    off_model = ScriptedModelGateway(
        [
            tool_response("test-read-existing", "get_recent_weight_trend", {"limit": 7}),
            text_response(legacy_after_rollback),
        ]
    )
    definition = AgentRuntimeDefinition(
        model_provider="test-provider",
        text_model="test-scripted-text",
        vision_model="test-scripted-vision",
        code_revision="test-rollback",
        multi_agent_mode="on",
        response_reviewer_enabled=True,
    )
    try:
        on_runtime = build_agent_runtime(
            database=database, model=on_model, definition=definition, clock=lambda: NOW
        )
        first = await on_runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="test-rollback-user",
                text="测试：今天体重77.6kg",
                source_message_id="test-message-before-rollback",
                occurred_at=NOW,
                deadline_at=NOW + timedelta(seconds=30),
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        assert first.termination is HarnessTermination.FINAL_RESPONSE
        assert first.final_text == adopted_text
        before = await WeightRepository(database).recent_trend("test-rollback-user")
        assert len(before.records) == 1
        record = before.records[0]
        assert record.weight_grams == 77_600
        assert record.source_turn_id == first.turn_id
        first_items = await HarnessStateRepository(database).list_items(first.turn_id)
        assert any(
            item.item_type is ItemType.RESPONSE_ADOPTED and item.payload.get("final")
            for item in first_items
        )
        assert [request.purpose for request in on_model.requests] == [
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.ORCHESTRATOR,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_REVIEWER,
        ]
        on_model.assert_exhausted()

        # Simulate restarting the process with the rollout flag reverted to off.
        await database.close()
        database = Database(database_url)
        off_runtime = build_agent_runtime(
            database=database,
            model=off_model,
            definition=definition.model_copy(
                update={
                    "multi_agent_mode": "off",
                    "response_reviewer_enabled": False,
                }
            ),
            clock=lambda: NOW + timedelta(minutes=1),
        )
        second = await off_runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="test-rollback-user",
                text="测试：刚才那条体重记录还在吗？不要新增。",
                source_message_id="test-message-after-rollback",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        assert second.termination is HarnessTermination.FINAL_RESPONSE
        assert second.final_text == legacy_after_rollback
        assert second.thread_id == first.thread_id
        assert second.turn_id != first.turn_id
        after = await WeightRepository(database).recent_trend("test-rollback-user")
        assert after.records == before.records
        read_receipt = json.loads(off_model.requests[1].messages[-1].content or "{}")
        assert read_receipt["status"] == "succeeded"
        assert read_receipt["output"]["current_record_id"] == record.id
        assert read_receipt["output"]["records"][0]["weight_kg"] == "77.6"
        assert all(request.purpose is ModelPurpose.HARNESS_TURN for request in off_model.requests)
        off_model.assert_exhausted()

        store = HarnessStateRepository(database)
        second_items = await store.list_items(second.turn_id)
        assert not any(item.item_type is ItemType.RESPONSE_ADOPTED for item in second_items)
        assert [
            item.payload["tool_name"]
            for item in second_items
            if item.item_type is ItemType.TOOL_CALL
        ] == ["get_recent_weight_trend"]
        finals = [item for item in second_items if item.item_type is ItemType.AGENT_MESSAGE]
        assert len(finals) == 1
        assert finals[0].payload["text"] == legacy_after_rollback
        for turn_id in (first.turn_id, second.turn_id):
            stored_turn = await store.get_turn(turn_id)
            assert stored_turn is not None and stored_turn.status is TurnStatus.COMPLETED
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(WeightRecord)) == 1
            assert await session.scalar(select(func.count()).select_from(AgentThreadRecord)) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentInvocationRecord)
                    .where(AgentInvocationRecord.turn_id == second.turn_id)
                )
                == 0
            )
    finally:
        await on_model.close()
        await off_model.close()
        await database.close()
