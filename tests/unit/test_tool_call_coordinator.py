from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.errors import PendingActionConfigurationError
from slim_guard.harness.events import (
    PendingActionType,
    TurnStatus,
    TurnTrigger,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.pending_actions import PendingActionRepository
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
    def __init__(self, execution: ToolExecution) -> None:
        self.execution = execution
        self.calls: list[NormalizedToolCall] = []

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
    ) -> ToolExecution:
        assert context.turn_id
        assert authorization.allowed_tool_names
        self.calls.append(call)
        return self.execution


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


def execution(decision: ToolPolicyDecision) -> ToolExecution:
    if decision is ToolPolicyDecision.ALLOW:
        result = ToolResult.success(output={"weight_kg": 77.6})
    else:
        result = ToolResult.failed(
            code=(
                "tool_confirmation_required"
                if decision is ToolPolicyDecision.CONFIRM
                else "tool_review_required"
            ),
            message="Approval is required before recording this weight.",
        )
    return ToolExecution(
        tool_call_id="call-1",
        tool_name="record_weight",
        tool_version="v1",
        canonical_arguments={"weight_kg": 77.6},
        idempotency_key="tool-execution-1",
        policy_decision=decision,
        result=result,
    )


def authorization() -> ToolAuthorization:
    return ToolAuthorization(allowed_tool_names=frozenset({"record_weight"}))


@pytest.mark.parametrize(
    ("decision", "action_type", "turn_status", "ttl"),
    [
        (
            ToolPolicyDecision.CONFIRM,
            PendingActionType.USER_CONFIRMATION,
            TurnStatus.WAITING_USER_CONFIRMATION,
            timedelta(minutes=10),
        ),
        (
            ToolPolicyDecision.REVIEW,
            PendingActionType.HUMAN_REVIEW,
            TurnStatus.WAITING_HUMAN_REVIEW,
            timedelta(hours=2),
        ),
    ],
)
async def test_policy_gate_creates_pending_action_and_pauses_turn(
    tmp_path,
    decision: ToolPolicyDecision,
    action_type: PendingActionType,
    turn_status: TurnStatus,
    ttl: timedelta,
) -> None:
    database, turn_state, turn, context = await prepare_state(tmp_path)
    pending_actions = PendingActionRepository(database)
    gateway = FakeToolGateway(execution(decision))
    now = datetime.now(UTC)
    coordinator = ToolCallCoordinator(
        gateway=gateway,
        pending_actions=pending_actions,
        turn_state=turn_state,
        confirmation_ttl=timedelta(minutes=10),
        review_ttl=timedelta(hours=2),
    )
    try:
        outcome = await coordinator.execute(
            call=NormalizedToolCall(
                id="call-1",
                name="record_weight",
                arguments={"weight_kg": 77.6},
            ),
            context=context,
            authorization=authorization(),
            source_item_id=None,
            now=now,
        )

        assert outcome.turn.status is turn_status
        assert outcome.pending_action is not None
        assert outcome.pending_action.thread_id == turn.thread_id
        assert outcome.pending_action.action_type is action_type
        assert outcome.pending_action.execution_key == "tool-execution-1"
        assert outcome.pending_action.canonical_arguments == {"weight_kg": 77.6}
        assert outcome.pending_action.expires_at == now + ttl
    finally:
        await database.close()


async def test_allowed_execution_does_not_create_pending_action_or_pause_turn(tmp_path) -> None:
    database, turn_state, turn, context = await prepare_state(tmp_path)
    pending_actions = PendingActionRepository(database)
    coordinator = ToolCallCoordinator(
        gateway=FakeToolGateway(execution(ToolPolicyDecision.ALLOW)),
        pending_actions=pending_actions,
        turn_state=turn_state,
        confirmation_ttl=timedelta(minutes=10),
        review_ttl=timedelta(hours=2),
    )
    try:
        outcome = await coordinator.execute(
            call=NormalizedToolCall(
                id="call-1",
                name="record_weight",
                arguments={"weight_kg": 77.6},
            ),
            context=context,
            authorization=authorization(),
            source_item_id=None,
            now=datetime.now(UTC),
        )

        assert outcome.turn.status is TurnStatus.RUNNING
        assert outcome.pending_action is None
        assert await pending_actions.list_open(
            thread_id=turn.thread_id,
            at=datetime.now(UTC),
        ) == []
    finally:
        await database.close()


async def test_gated_execution_requires_a_complete_frozen_command(tmp_path) -> None:
    database, turn_state, _, context = await prepare_state(tmp_path)
    incomplete = ToolExecution(
        tool_call_id="call-1",
        tool_name="record_weight",
        policy_decision=ToolPolicyDecision.CONFIRM,
        result=ToolResult.failed(
            code="tool_confirmation_required",
            message="Confirmation required.",
        ),
    )
    coordinator = ToolCallCoordinator(
        gateway=FakeToolGateway(incomplete),
        pending_actions=PendingActionRepository(database),
        turn_state=turn_state,
        confirmation_ttl=timedelta(minutes=10),
        review_ttl=timedelta(hours=2),
    )
    try:
        with pytest.raises(PendingActionConfigurationError, match="frozen command"):
            await coordinator.execute(
                call=NormalizedToolCall(
                    id="call-1",
                    name="record_weight",
                    arguments={"weight_kg": 77.6},
                ),
                context=context,
                authorization=authorization(),
                source_item_id=None,
                now=datetime.now(UTC),
            )
    finally:
        await database.close()
