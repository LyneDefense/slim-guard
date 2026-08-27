from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.failures import HarnessFailure
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository, TurnRef
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.tools.contracts import ToolExecution, ToolPolicyDecision, ToolResult


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


async def prepare_turn(tmp_path) -> tuple[Database, HarnessStateRepository, TurnRef]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'harness-trace.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    user = SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now)
    async with database.session() as session, session.begin():
        session.add(user)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    turn = await repository.start_turn(
        user_id=user.id,
        agent_version_id=manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
    )
    return database, repository, turn


def model_request() -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.HARNESS_TURN,
        model="glm-5.2",
        messages=(ModelMessage(role=MessageRole.USER, content="今天 77.6kg"),),
    )


async def test_persistent_recorder_saves_final_response_and_completes_turn(tmp_path) -> None:
    database, repository, turn = await prepare_turn(tmp_path)
    recorder = PersistentHarnessRunRecorder(repository)
    response = ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content="已记录。"),
        finish_reason="stop",
        usage=ModelUsage(input_tokens=10, output_tokens=3, total_tokens=13),
        provider_request_id="provider-1",
    )
    try:
        await recorder.record_model_response(
            turn_id=turn.id,
            request=model_request(),
            response=response,
            call_index=1,
        )
        await recorder.finish_run(
            turn_id=turn.id,
            termination=HarnessTermination.FINAL_RESPONSE,
            final_text="已记录。",
            model_call_count=1,
            tool_call_count=0,
            total_token_count=13,
            failure=None,
        )

        items = await repository.list_items(turn.id)
        stored_turn = await repository.get_turn(turn.id)

        assert [item.item_type for item in items] == [
            ItemType.MODEL_MESSAGE,
            ItemType.AGENT_MESSAGE,
        ]
        assert items[0].payload["usage"]["total_tokens"] == 13
        assert items[0].payload["provider_request_id"] == "provider-1"
        assert items[1].payload == {"text": "已记录。"}
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.COMPLETED
    finally:
        await database.close()


async def test_tool_result_reserved_before_wait_can_finish_after_pause(tmp_path) -> None:
    database, repository, turn = await prepare_turn(tmp_path)
    recorder = PersistentHarnessRunRecorder(repository)
    call = NormalizedToolCall(
        id="call-1",
        name="record_weight",
        arguments={"weight_kg": 77.6},
    )
    try:
        trace = await recorder.start_tool_call(
            turn_id=turn.id,
            call=call,
            call_index=1,
        )
        waiting_turn = await repository.transition_turn(
            turn_id=turn.id,
            target=TurnStatus.WAITING_USER_CONFIRMATION,
        )
        outcome = ToolCallOutcome(
            execution=ToolExecution(
                tool_call_id=call.id,
                tool_name=call.name,
                tool_version="v1",
                canonical_arguments=call.arguments,
                idempotency_key="execution-1",
                policy_decision=ToolPolicyDecision.CONFIRM,
                result=ToolResult.failed(
                    code="tool_confirmation_required",
                    message="Please confirm this action.",
                ),
            ),
            turn=waiting_turn,
            pending_action=None,
        )

        await recorder.finish_tool_call(trace=trace, outcome=outcome)
        await recorder.finish_run(
            turn_id=turn.id,
            termination=HarnessTermination.WAITING_USER_CONFIRMATION,
            final_text=None,
            model_call_count=1,
            tool_call_count=1,
            total_token_count=0,
            failure=None,
        )

        items = await repository.list_items(turn.id)

        assert [item.item_type for item in items] == [
            ItemType.TOOL_CALL,
            ItemType.TOOL_RESULT,
        ]
        assert [item.status for item in items] == [
            ItemStatus.COMPLETED,
            ItemStatus.COMPLETED,
        ]
        assert items[1].payload["execution"]["result"]["failure"]["code"] == (
            "tool_confirmation_required"
        )
        assert (await repository.get_turn(turn.id)) == waiting_turn
    finally:
        await database.close()


async def test_budget_termination_is_audited_and_suspends_turn(tmp_path) -> None:
    database, repository, turn = await prepare_turn(tmp_path)
    recorder = PersistentHarnessRunRecorder(repository)
    try:
        await recorder.finish_run(
            turn_id=turn.id,
            termination=HarnessTermination.MAX_MODEL_CALLS,
            final_text=None,
            model_call_count=6,
            tool_call_count=5,
            total_token_count=900,
            failure=None,
        )

        items = await repository.list_items(turn.id)
        stored_turn = await repository.get_turn(turn.id)

        assert len(items) == 1
        assert items[0].item_type is ItemType.ERROR
        assert items[0].status is ItemStatus.FAILED
        assert items[0].payload == {
            "code": "max_model_calls",
            "model_call_count": 6,
            "tool_call_count": 5,
            "total_token_count": 900,
            "failure": None,
        }
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.SUSPENDED
    finally:
        await database.close()


@pytest.mark.parametrize(
    ("retryable", "expected_status"),
    (
        (True, TurnStatus.SUSPENDED),
        (False, TurnStatus.FAILED),
    ),
)
async def test_fatal_error_status_depends_on_retryability(
    tmp_path,
    retryable: bool,
    expected_status: TurnStatus,
) -> None:
    database, repository, turn = await prepare_turn(tmp_path)
    recorder = PersistentHarnessRunRecorder(repository)
    failure = HarnessFailure(
        code="model_timeout" if retryable else "unsupported_model_feature",
        error_type="TestModelError",
        retryable=retryable,
    )
    try:
        await recorder.finish_run(
            turn_id=turn.id,
            termination=HarnessTermination.FATAL_ERROR,
            final_text=None,
            model_call_count=0,
            tool_call_count=0,
            total_token_count=0,
            failure=failure,
        )

        items = await repository.list_items(turn.id)
        stored_turn = await repository.get_turn(turn.id)

        assert items[0].payload["failure"] == failure.to_payload()
        assert "message" not in items[0].payload["failure"]
        assert stored_turn is not None
        assert stored_turn.status is expected_status
    finally:
        await database.close()
