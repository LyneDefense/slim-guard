"""Synthetic interruption regressions: trace state must terminate with the graph."""

import asyncio
from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelResponse,
    ModelUsage,
)
from slim_guard.agents.contracts import InvocationStatus
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.orchestration.coordinator import AgentWorkflowCoordinator, ShadowWorkflowRequest
from slim_guard.orchestration.repository import InvocationBudgetExceeded


class Recorder(NullHarnessRunRecorder):
    def __init__(self):
        self.events = []

    async def record_workflow_event(self, *, event_type, payload, **kwargs):
        self.events.append((event_type, payload))


class Persistence:
    def __init__(self):
        self.invocations = {}
        self.results = {}

    async def start_invocation(self, invocation, **kwargs):
        self.invocations[invocation.invocation_id] = invocation

    async def append_artifact(self, artifact, **kwargs):
        return artifact

    async def complete_invocation(self, result, **kwargs):
        self.results[result.invocation_id] = result


class BudgetRejectingPersistence(Persistence):
    async def complete_invocation(self, result, **kwargs):
        if result.status is InvocationStatus.SUCCEEDED:
            raise InvocationBudgetExceeded("synthetic token budget mismatch")
        await super().complete_invocation(result, **kwargs)


class BrokenModel:
    def __init__(self, *, stall):
        self.stall = stall
        self.started = asyncio.Event()

    async def complete(self, request):
        self.started.set()
        if self.stall:
            await asyncio.Event().wait()
        raise RuntimeError("synthetic unexpected provider error")


@pytest.mark.parametrize("cancel", [False, True])
async def test_unexpected_error_or_cancellation_closes_started_invocation(cancel):
    recorder = Recorder()
    persistence = Persistence()
    model = BrokenModel(stall=cancel)
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=recorder,
        persistence=persistence,
        model_name="TEST-ONLY",
        graph_version="TEST-ONLY",
    )
    task = asyncio.create_task(
        coordinator.run_shadow(
            ShadowWorkflowRequest(
                turn_id="test-turn",
                trace_id="test-trace",
                context=(ModelMessage(role=MessageRole.USER, content="测试输入"),),
            )
        )
    )
    await asyncio.wait_for(model.started.wait(), 1)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result.status is InvocationStatus.FAILED
    assert persistence.invocations.keys() == persistence.results.keys()
    assert len(persistence.results) == 1
    result = next(iter(persistence.results.values()))
    assert result.status is InvocationStatus.FAILED
    assert result.failure_code == ("workflow_cancelled" if cancel else "workflow_interrupted")
    assert result.output_schema == "TurnDirective"
    assert any(event == ItemType.INVOCATION_RESULT for event, _ in recorder.events)


async def test_persistence_cleanup_failure_does_not_replace_legacy_failure():
    class BrokenPersistence(Persistence):
        async def complete_invocation(self, result, **kwargs):
            raise RuntimeError("synthetic unavailable database")

    coordinator = AgentWorkflowCoordinator(
        model=BrokenModel(stall=False),
        recorder=Recorder(),
        persistence=BrokenPersistence(),
        model_name="TEST-ONLY",
        graph_version="TEST-ONLY",
        clock=lambda: datetime(2026, 9, 8, tzinfo=UTC),
    )
    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            turn_id="test-turn",
            trace_id="test-trace",
            legacy_response="原回复",
            context=(ModelMessage(role=MessageRole.USER, content="测试输入"),),
        )
    )
    assert result.status is InvocationStatus.FAILED
    assert result.legacy_response == "原回复"


async def test_budget_persistence_rejection_is_not_masked_as_generic_interruption():
    recorder = Recorder()
    persistence = BudgetRejectingPersistence()

    class SuccessfulModel:
        async def complete(self, request):
            return ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=(
                        '{"response_path":"direct","interaction_kind":"chat",'
                        '"user_need_summary":"测试","response_brief":"收到。",'
                        '"evidence_refs":[],"dish_names":[],'
                        '"voice_act":"acknowledge","requested_detail":"short",'
                        '"resolves_pending_dish_confirmation":false}'
                    ),
                ),
                usage=ModelUsage(input_tokens=20, output_tokens=20, total_tokens=40),
                finish_reason="stop",
            )

    coordinator = AgentWorkflowCoordinator(
        model=SuccessfulModel(),
        recorder=recorder,
        persistence=persistence,
        model_name="TEST-ONLY",
        graph_version="TEST-ONLY",
    )

    with pytest.raises(RuntimeError, match="synthetic token budget mismatch"):
        await coordinator.run_shadow(
            ShadowWorkflowRequest(
                turn_id="test-turn",
                trace_id="test-trace",
                context=(ModelMessage(role=MessageRole.USER, content="测试输入"),),
            )
        )

    assert len(persistence.results) == 1
    result = next(iter(persistence.results.values()))
    assert result.status is InvocationStatus.FAILED
    assert result.failure_code == "invocation_budget_persistence_rejected"
