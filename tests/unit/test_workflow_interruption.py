"""Synthetic interruption regressions: trace state must terminate with the graph."""

import asyncio
from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.gateway import MessageRole, ModelMessage
from slim_guard.agents.contracts import InvocationStatus
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.orchestration.coordinator import AgentWorkflowCoordinator, ShadowWorkflowRequest


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

    async def complete_invocation(self, result, **kwargs):
        self.results[result.invocation_id] = result


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
    assert result.failure_code == "workflow_interrupted"
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
