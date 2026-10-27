from __future__ import annotations

from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.errors import TurnNotWritable
from slim_guard.harness.events import TurnStatus, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.tools.contracts import (
    ToolContext,
    ToolExecution,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolResult,
)
from slim_guard.tools.policy import ToolAuthorization


class FakeToolGateway:
    def __init__(self, result: ToolExecution) -> None:
        self.result = result
        self.calls = []

    async def execute(self, *, call, context, authorization) -> ToolExecution:
        self.calls.append((call, context, authorization))
        return self.result


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


async def prepare_state(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'tool-coordinator.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    agent_manifest = manifest()
    await AgentVersionRepository(database).register(agent_manifest)
    turn_state = HarnessStateRepository(database)
    turn = await turn_state.start_turn(
        user_id="user-1",
        agent_version_id=agent_manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
    )
    context = ToolContext(
        thread_id=turn.thread_id,
        turn_id=turn.id,
        tool_call_id="call-1",
        user_id="user-1",
        agent_version_id=agent_manifest.version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
    )
    return database, turn_state, turn, context


def successful_execution() -> ToolExecution:
    return ToolExecution(
        tool_call_id="call-1",
        tool_name="record_weight",
        tool_version="v1",
        canonical_arguments={"weight_kg": 77.6},
        idempotency_key="tool-execution-1",
        policy_decision=ToolPolicyDecision.ALLOW,
        result=ToolResult.success(output={"weight_kg": 77.6}),
    )


def tool_call() -> NormalizedToolCall:
    return NormalizedToolCall(
        id="call-1",
        name="record_weight",
        arguments={"weight_kg": 77.6},
    )


def authorization() -> ToolAuthorization:
    return ToolAuthorization(allowed_tool_names=frozenset({"record_weight"}))


async def test_running_turn_delegates_tool_call_and_returns_execution(tmp_path) -> None:
    database, turn_state, turn, context = await prepare_state(tmp_path)
    gateway = FakeToolGateway(successful_execution())
    coordinator = ToolCallCoordinator(gateway=gateway, turn_state=turn_state)
    now = datetime.now(UTC)
    call = tool_call()
    grants = authorization()
    try:
        outcome = await coordinator.execute(
            call=call,
            context=context,
            authorization=grants,
            source_item_id=None,
            now=now,
        )

        assert outcome.execution is gateway.result
        assert outcome.turn == turn
        assert gateway.calls == [(call, context, grants)]
    finally:
        await database.close()


async def test_unknown_turn_is_rejected_before_gateway_call(tmp_path) -> None:
    database, turn_state, _, context = await prepare_state(tmp_path)
    gateway = FakeToolGateway(successful_execution())
    coordinator = ToolCallCoordinator(gateway=gateway, turn_state=turn_state)
    try:
        with pytest.raises(LookupError, match="Agent turn not found"):
            await coordinator.execute(
                call=tool_call(),
                context=context.model_copy(update={"turn_id": "missing-turn"}),
                authorization=authorization(),
                source_item_id=None,
                now=datetime.now(UTC),
            )
        assert gateway.calls == []
    finally:
        await database.close()


@pytest.mark.parametrize(
    "context_update",
    [
        {"thread_id": "different-thread"},
        {"agent_version_id": "different-version"},
    ],
)
async def test_context_mismatch_is_rejected_before_gateway_call(tmp_path, context_update) -> None:
    database, turn_state, _, context = await prepare_state(tmp_path)
    gateway = FakeToolGateway(successful_execution())
    coordinator = ToolCallCoordinator(gateway=gateway, turn_state=turn_state)
    try:
        with pytest.raises(TurnNotWritable, match="does not match"):
            await coordinator.execute(
                call=tool_call(),
                context=context.model_copy(update=context_update),
                authorization=authorization(),
                source_item_id=None,
                now=datetime.now(UTC),
            )
        assert gateway.calls == []
    finally:
        await database.close()


async def test_non_running_turn_is_rejected_before_gateway_call(tmp_path) -> None:
    database, turn_state, turn, context = await prepare_state(tmp_path)
    await turn_state.transition_turn(
        turn_id=turn.id,
        target=TurnStatus.COMPLETED,
        expected=TurnStatus.RUNNING,
    )
    gateway = FakeToolGateway(successful_execution())
    coordinator = ToolCallCoordinator(gateway=gateway, turn_state=turn_state)
    try:
        with pytest.raises(TurnNotWritable, match="Cannot execute a tool"):
            await coordinator.execute(
                call=tool_call(),
                context=context,
                authorization=authorization(),
                source_item_id=None,
                now=datetime.now(UTC),
            )
        assert gateway.calls == []
    finally:
        await database.close()
