from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import PendingActionStatus, TurnStatus, TurnTrigger
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.pending_resume import PendingActionResumeCoordinator
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolResult,
)
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolExecutor, ToolGateway
from slim_guard.tools.policy import DefaultToolPolicy, ToolAuthorization
from slim_guard.tools.registry import RegisteredTool, ToolRegistry


class WeightArguments(ToolArguments):
    weight_kg: float


@dataclass(slots=True)
class Runtime:
    database: Database
    context: ToolContext
    tool_calls: ToolCallCoordinator
    resume: PendingActionResumeCoordinator
    authorization: ToolAuthorization
    handler_calls: list[WeightArguments]


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


async def prepare_runtime(
    tmp_path,
    *,
    effect_level: ToolEffectLevel,
    requires_confirmation: bool,
) -> Runtime:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'pending-resume.sqlite3'}")
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
    tool = RegisteredTool(
        name="record_weight",
        description="Record a weight explicitly provided by the current user.",
        version="v1",
        arguments_model=WeightArguments,
        effect_level=effect_level,
        idempotent=True,
        requires_confirmation=requires_confirmation,
        timeout_seconds=1,
    )
    handler_calls: list[WeightArguments] = []

    async def record_weight(_: ToolContext, arguments: WeightArguments) -> ToolResult:
        handler_calls.append(arguments)
        return ToolResult.success(
            output={"weight_kg": arguments.weight_kg},
            source_ids=("weight-1",),
        )

    gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=record_weight,
            )
        },
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    pending_actions = PendingActionRepository(database)
    tool_calls = ToolCallCoordinator(
        gateway=gateway,
        pending_actions=pending_actions,
        turn_state=turn_state,
        confirmation_ttl=timedelta(minutes=10),
        review_ttl=timedelta(hours=2),
    )
    return Runtime(
        database=database,
        context=context,
        tool_calls=tool_calls,
        resume=PendingActionResumeCoordinator(
            pending_actions=pending_actions,
            turn_state=turn_state,
            tool_calls=tool_calls,
        ),
        authorization=ToolAuthorization(
            allowed_tool_names=frozenset({tool.name}),
            isolated_write_environment=True,
        ),
        handler_calls=handler_calls,
    )


def call() -> NormalizedToolCall:
    return NormalizedToolCall(
        id="call-1",
        name="record_weight",
        arguments={"weight_kg": 77.6},
    )


async def test_user_approval_executes_frozen_command_once_and_consumes_action(
    tmp_path,
) -> None:
    runtime = await prepare_runtime(
        tmp_path,
        effect_level=ToolEffectLevel.SENSITIVE_WRITE,
        requires_confirmation=False,
    )
    now = datetime.now(UTC)
    try:
        gated = await runtime.tool_calls.execute(
            call=call(),
            context=runtime.context,
            authorization=runtime.authorization,
            source_item_id=None,
            now=now,
        )
        assert gated.pending_action is not None
        assert gated.turn.status is TurnStatus.WAITING_USER_CONFIRMATION
        assert runtime.handler_calls == []

        resumed = await runtime.resume.resolve(
            action_id=gated.pending_action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            now=now + timedelta(seconds=1),
        )
        repeated = await runtime.resume.resolve(
            action_id=gated.pending_action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            now=now + timedelta(seconds=2),
        )

        assert resumed.action.status is PendingActionStatus.CONSUMED
        assert resumed.turn.status is TurnStatus.RUNNING
        assert resumed.tool_outcome is not None
        assert resumed.tool_outcome.execution.policy_decision is ToolPolicyDecision.ALLOW
        assert runtime.handler_calls == [WeightArguments(weight_kg=77.6)]
        assert repeated.action.status is PendingActionStatus.CONSUMED
        assert repeated.tool_outcome is None
        assert runtime.handler_calls == [WeightArguments(weight_kg=77.6)]
    finally:
        await runtime.database.close()


async def test_user_rejection_resumes_turn_without_executing_tool(tmp_path) -> None:
    runtime = await prepare_runtime(
        tmp_path,
        effect_level=ToolEffectLevel.SENSITIVE_WRITE,
        requires_confirmation=False,
    )
    now = datetime.now(UTC)
    try:
        gated = await runtime.tool_calls.execute(
            call=call(),
            context=runtime.context,
            authorization=runtime.authorization,
            source_item_id=None,
            now=now,
        )
        assert gated.pending_action is not None

        rejected = await runtime.resume.resolve(
            action_id=gated.pending_action.id,
            resolution=PendingActionStatus.REJECTED,
            resolved_by="user-1",
            now=now + timedelta(seconds=1),
        )

        assert rejected.action.status is PendingActionStatus.REJECTED
        assert rejected.turn.status is TurnStatus.RUNNING
        assert rejected.tool_outcome is None
        assert runtime.handler_calls == []
    finally:
        await runtime.database.close()


async def test_review_then_confirmation_uses_both_grants_before_execution(tmp_path) -> None:
    runtime = await prepare_runtime(
        tmp_path,
        effect_level=ToolEffectLevel.EXTERNAL_EFFECT,
        requires_confirmation=True,
    )
    now = datetime.now(UTC)
    try:
        review_gate = await runtime.tool_calls.execute(
            call=call(),
            context=runtime.context,
            authorization=runtime.authorization,
            source_item_id=None,
            now=now,
        )
        assert review_gate.pending_action is not None
        assert review_gate.turn.status is TurnStatus.WAITING_HUMAN_REVIEW

        reviewed = await runtime.resume.resolve(
            action_id=review_gate.pending_action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="reviewer-1",
            now=now + timedelta(seconds=1),
        )
        assert reviewed.action.status is PendingActionStatus.APPROVED
        assert reviewed.tool_outcome is not None
        confirmation = reviewed.tool_outcome.pending_action
        assert confirmation is not None
        assert reviewed.turn.status is TurnStatus.WAITING_USER_CONFIRMATION
        assert runtime.handler_calls == []

        confirmed = await runtime.resume.resolve(
            action_id=confirmation.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            now=now + timedelta(seconds=2),
        )
        related = await PendingActionRepository(runtime.database).list_for_execution(
            confirmation.execution_key
        )

        assert confirmed.action.status is PendingActionStatus.CONSUMED
        assert confirmed.turn.status is TurnStatus.RUNNING
        assert runtime.handler_calls == [WeightArguments(weight_kg=77.6)]
        assert {item.status for item in related} == {PendingActionStatus.CONSUMED}
    finally:
        await runtime.database.close()


async def test_resume_recovers_after_approval_and_turn_transition_were_already_saved(
    tmp_path,
) -> None:
    runtime = await prepare_runtime(
        tmp_path,
        effect_level=ToolEffectLevel.SENSITIVE_WRITE,
        requires_confirmation=False,
    )
    now = datetime.now(UTC)
    pending_actions = PendingActionRepository(runtime.database)
    turn_state = HarnessStateRepository(runtime.database)
    try:
        gated = await runtime.tool_calls.execute(
            call=call(),
            context=runtime.context,
            authorization=runtime.authorization,
            source_item_id=None,
            now=now,
        )
        assert gated.pending_action is not None
        await pending_actions.resolve(
            action_id=gated.pending_action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            resolved_at=now + timedelta(seconds=1),
        )
        await turn_state.transition_turn(
            turn_id=runtime.context.turn_id,
            target=TurnStatus.RUNNING,
            expected=TurnStatus.WAITING_USER_CONFIRMATION,
        )

        recovered = await runtime.resume.resolve(
            action_id=gated.pending_action.id,
            resolution=PendingActionStatus.APPROVED,
            resolved_by="user-1",
            now=now + timedelta(seconds=2),
        )

        assert recovered.action.status is PendingActionStatus.CONSUMED
        assert recovered.tool_outcome is not None
        assert runtime.handler_calls == [WeightArguments(weight_kg=77.6)]
    finally:
        await runtime.database.close()
