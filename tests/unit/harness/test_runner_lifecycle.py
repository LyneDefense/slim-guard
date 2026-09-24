"""Turn ownership, Core invocation boundaries and response policy regression."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
)
from slim_guard.agents.core import CoreAgent
from slim_guard.db.models import AgentTurnRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.group_chat.contracts import ChatMessage
from slim_guard.harness.context import ContextCompiler
from slim_guard.harness.events import ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.preparation import TurnContextPreparer
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.runner import TurnHarness
from slim_guard.harness.safety import OutputGuardResult
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.response_pipeline.contracts import ResponseFinalizationResult
from slim_guard.response_pipeline.processor import ResponseProcessor
from slim_guard.runtime.invocation.core import CoreInvocationRunner
from slim_guard.tools.contracts import ToolExecution, ToolExecutionMode, ToolResult
from slim_guard.tools.registry import ToolRegistry
from slim_guard.usage.context import CURRENT

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def response(text="中性草稿。", *, calls=()):
    return ModelResponse(
        message=ModelMessage(role="assistant", content=text, tool_calls=calls),
        usage=ModelUsage(total_tokens=7),
    )


class NoTools:
    def requires_waiting(self, **kwargs):
        return False

    async def finalize_batch(self, **kwargs):
        return None

    async def execute(self, **kwargs):
        raise AssertionError("Unexpected tool execution")


class Recorder(PersistentHarnessRunRecorder):
    def __init__(self, state):
        super().__init__(state)
        self.finishes = []
        self.fail_after_finish = False

    async def finish_run(self, **kwargs):
        self.finishes.append(kwargs)
        await super().finish_run(**kwargs)
        if self.fail_after_finish:
            raise RuntimeError("record transport failed after commit")


@pytest.fixture
async def database(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    try:
        await db.create_schema()
        yield db
    finally:
        await db.close()


@pytest.fixture
async def env(database):
    db = database
    async with db.session() as session, session.begin():
        session.add(SlimGuardUser(id="user", first_seen_at=NOW, last_seen_at=NOW))
    manifest = AgentManifest.build(
        model_provider="test",
        model_parameters={},
        text_model="test",
        vision_model="test",
        tool_versions={},
        system_prompt_version="1",
        system_prompt="test",
        context_policy_version="1",
        memory_policy_version="1",
        compaction_policy_version="1",
        safety_policy_version="1",
        code_revision="test",
    )
    await AgentVersionRepository(db).register(manifest)
    state = HarnessStateRepository(db)
    invocations = OrchestrationRepository(db)
    recorder = Recorder(state)
    request = TurnInitializationRequest(
        user_id="user",
        agent_version_id=manifest.version_id,
        trigger=TurnTrigger.USER_MESSAGE,
        execution_mode=ToolExecutionMode.EVALUATION,
        inputs=(TurnInput.user_message(text="你好"),),
    )

    def build(*, model=None, finalizer=None, guard=None, data=None, tools=None):
        return TurnHarness(
            initializer=TurnInitializer(state),
            preparation=TurnContextPreparer(
                compiler=ContextCompiler(
                    manifest=manifest, system_prompt="test", tools=ToolRegistry(())
                ),
                recorder=recorder,
                context_data=data,
            ),
            core=CoreInvocationRunner(
                agent=CoreAgent(
                    model=model or ScriptedModelGateway([response()]),
                    tool_calls=tools or NoTools(),
                    recorder=recorder,
                    clock=lambda: NOW,
                ),
                store=invocations,
                clock=lambda: NOW,
            ),
            responses=ResponseProcessor(recorder=recorder, finalizer=finalizer, output_guard=guard),
            recorder=recorder,
            clock=lambda: NOW,
        )

    yield SimpleNamespace(
        db=db, state=state, invocations=invocations, recorder=recorder, request=request, build=build
    )


class Finalizer:
    async def finalize(self, request):
        return ResponseFinalizationResult(
            text="教练回复。",
            status="succeeded",
            core_output_artifact_id="plan",
            final_output_artifact_id="final",
            style_profile_version="doctor",
            model_call_count=2,
            total_token_count=30,
            messages=(
                ChatMessage(
                    id="coach",
                    turn_id=request.turn_id,
                    sequence=0,
                    participant="coach",
                    kind="text",
                    text="教练回复。",
                    created_at=NOW,
                ),
            ),
        )


async def test_core_finishes_before_reply_but_turn_stays_running(env):
    class ObserveFinalizer(Finalizer):
        async def finalize(self, request):
            (core,) = await env.invocations.list_turn_invocations(request.turn_id)
            turn = await env.state.get_turn(request.turn_id)
            assert core.status == "succeeded" and core.completed_at is not None
            assert turn.status is TurnStatus.RUNNING
            assert env.recorder.finishes == []
            draft = await env.invocations.get_artifact(core.output_artifact_id)
            assert draft.artifact_type == "core_response"
            assert draft.payload == {"text": "中性草稿。"}
            assert CURRENT.get().user_id == "user"
            assert CURRENT.get().invocation_id == core.invocation_id
            return await super().finalize(request)

    model = ScriptedModelGateway([response()])
    result = await env.build(model=model, finalizer=ObserveFinalizer()).run(request=env.request)
    assert result.final_text == "教练回复。"
    assert len(model.requests) == 1
    assert result.model_call_count == 3 and result.total_token_count == 37
    assert result.loop.model_call_count == 1 and result.loop.total_token_count == 7
    (core,) = await env.invocations.list_turn_invocations(result.initialized.turn.id)
    assert core.model_call_count == 1 and core.total_token_count == 7
    assert len(env.recorder.finishes) == 1
    items = await env.state.list_items(result.initialized.turn.id)
    messages = [i for i in items if i.item_type is ItemType.AGENT_MESSAGE]
    assert len(messages) == 1 and messages[0].payload["participant"] == "coach"
    assert CURRENT.get().turn_id is None


async def test_reply_failure_preserves_core_draft_without_regeneration(env):
    class BrokenFinalizer:
        async def finalize(self, request):
            raise RuntimeError("unavailable")

    model = ScriptedModelGateway([response()])
    result = await env.build(model=model, finalizer=BrokenFinalizer()).run(request=env.request)
    assert result.final_text == "中性草稿。" and len(model.requests) == 1
    assert result.loop.model_call_count == 1
    items = await env.state.list_items(result.initialized.turn.id)
    degraded = [i for i in items if i.item_type is ItemType.RESPONSE_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].payload["reason_code"] == "response_pipeline_failed"
    assert len(env.recorder.finishes) == 1


@pytest.mark.parametrize("blocked_text", ["中性草稿。", "教练回复。"])
async def test_output_guard_policy_is_applied_once_per_stage(env, blocked_text):
    class Guard:
        texts = []

        def review(self, *, text, **kwargs):
            self.texts.append(text)
            return OutputGuardResult(
                "安全提示。" if text == blocked_text else text, text == blocked_text, "test_guard"
            )

    guard = Guard()
    result = await env.build(finalizer=Finalizer(), guard=guard).run(request=env.request)
    expected = ["中性草稿。"] if blocked_text == "中性草稿。" else ["中性草稿。", "教练回复。"]
    assert guard.texts == expected
    assert result.final_text == ("安全提示。" if len(expected) == 1 else "中性草稿。")
    assert result.outgoing_messages == ()
    assert result.final_output_artifact_id is None
    assert len(env.recorder.finishes) == 1


async def test_unknown_core_exception_is_rethrown_with_completed_usage(env):
    error = RuntimeError("do not persist this sensitive message")

    class BrokenTools:
        def requires_waiting(self, **kwargs):
            return False

        async def finalize_batch(self, **kwargs):
            return None

        async def execute(self, **kwargs):
            raise error

    model = ScriptedModelGateway(
        [response(None, calls=(NormalizedToolCall(id="call", name="x", arguments={}),))]
    )
    with pytest.raises(RuntimeError) as caught:
        await env.build(model=model, tools=BrokenTools()).run(request=env.request)
    assert caught.value is error
    (finish,) = env.recorder.finishes
    assert finish["model_call_count"] == 1 and finish["total_token_count"] == 7
    (core,) = await env.invocations.list_turn_invocations(finish["turn_id"])
    assert core.status == "failed" and core.model_call_count == 1
    assert core.failure_code == "core_execution_failed"
    assert (await env.state.get_turn(finish["turn_id"])).status is TurnStatus.FAILED
    items = await env.state.list_items(finish["turn_id"])
    assert not any(i.item_type is ItemType.AGENT_MESSAGE for i in items)
    assert not any("sensitive message" in str(i.payload) for i in items)


@pytest.mark.parametrize("stage", ["context", "core", "reply"])
async def test_cancellation_propagates_and_closes_started_work(env, stage):
    entered = asyncio.Event()

    async def block():
        entered.set()
        await asyncio.Event().wait()

    class BlockData:
        async def load(self, **kwargs):
            await block()

    class BlockModel:
        async def complete(self, request):
            await block()

    class BlockFinalizer:
        async def finalize(self, request):
            await block()

    runner = env.build(
        data=BlockData() if stage == "context" else None,
        model=BlockModel() if stage == "core" else None,
        finalizer=BlockFinalizer() if stage == "reply" else None,
    )
    task = asyncio.create_task(runner.run(request=env.request))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (finish,) = env.recorder.finishes
    assert finish["failure"].code == "execution_cancelled"
    assert finish["final_text"] is None
    turn = await env.state.get_turn(finish["turn_id"])
    assert turn.status is TurnStatus.SUSPENDED
    invocations = await env.invocations.list_turn_invocations(turn.id)
    if stage == "context":
        assert invocations == () or invocations == []
    else:
        assert invocations[0].status == ("succeeded" if stage == "reply" else "failed")
    async with env.db.session() as session:
        assert await session.scalar(select(AgentTurnRecord.status)) == "suspended"


async def test_finish_failure_does_not_duplicate_user_messages(env):
    env.recorder.fail_after_finish = True
    with pytest.raises(RuntimeError, match="after commit"):
        await env.build().run(request=env.request)
    (finish,) = env.recorder.finishes
    items = await env.state.list_items(finish["turn_id"])
    assert sum(i.item_type is ItemType.AGENT_MESSAGE for i in items) == 1
    assert (await env.state.get_turn(finish["turn_id"])).status is TurnStatus.COMPLETED


async def test_core_cancellation_retains_already_completed_model_usage(env):
    class CancelTools:
        def requires_waiting(self, **kwargs):
            return False

        async def finalize_batch(self, **kwargs):
            return None

        async def execute(self, **kwargs):
            raise asyncio.CancelledError()

    model = ScriptedModelGateway(
        [response(None, calls=(NormalizedToolCall(id="call", name="x", arguments={}),))]
    )
    with pytest.raises(asyncio.CancelledError):
        await env.build(model=model, tools=CancelTools()).run(request=env.request)
    (finish,) = env.recorder.finishes
    (core,) = await env.invocations.list_turn_invocations(finish["turn_id"])
    assert core.model_call_count == 1 and core.total_token_count == 7
    assert finish["model_call_count"] == 1 and finish["total_token_count"] == 7
    assert core.failure_code == "execution_cancelled"


@pytest.mark.parametrize(
    "waiting", [TurnStatus.WAITING_USER_CONFIRMATION, TurnStatus.WAITING_HUMAN_REVIEW]
)
async def test_waiting_tool_operation_is_not_completed_as_a_reply(env, waiting):
    class WaitingTools:
        def requires_waiting(self, **kwargs):
            return False

        async def finalize_batch(self, **kwargs):
            return waiting

        async def execute(self, *, call, context, **kwargs):
            turn = await env.state.transition_turn(
                turn_id=context.turn_id, target=waiting, expected=TurnStatus.RUNNING
            )
            return ToolCallOutcome(
                execution=ToolExecution(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    tool_version="1",
                    canonical_arguments={},
                    idempotency_key="key",
                    policy_decision="confirm",
                    result=ToolResult.failed(
                        code="confirmation_required", message="确认", retryable=False
                    ),
                ),
                turn=turn,
                pending_action=None,
            )

    class MustNotFinalize:
        async def finalize(self, request):
            raise AssertionError("Waiting Turns have no final reply")

    model = ScriptedModelGateway(
        [response(None, calls=(NormalizedToolCall(id="call", name="x", arguments={}),))]
    )
    result = await env.build(model=model, tools=WaitingTools(), finalizer=MustNotFinalize()).run(
        request=env.request
    )
    assert result.termination.value == waiting.value
    assert result.response_finalization is None and result.final_text is None
    assert (await env.state.get_turn(result.initialized.turn.id)).status is waiting
    assert len(env.recorder.finishes) == 1
    (core,) = await env.invocations.list_turn_invocations(result.initialized.turn.id)
    assert core.status == "degraded"
    items = await env.state.list_items(result.initialized.turn.id)
    assert not any(i.item_type is ItemType.AGENT_MESSAGE for i in items)
