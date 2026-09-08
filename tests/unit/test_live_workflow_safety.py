"""Live adoption boundaries with synthetic inputs and deterministic model/tool doubles."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ToolDefinition,
)
from slim_guard.agents.contracts import AgentArtifact, ArtifactProducerRole, InvocationStatus
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.harness.context import CompiledContext
from slim_guard.harness.events import ItemStatus, ItemType, ThreadStatus, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import InitializedTurn, TurnInitializationRequest, TurnInput
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.loop import HarnessTurnContext
from slim_guard.harness.runner import HarnessTurnRunner
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import ItemRef, ThreadRef, TurnRef
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.orchestration.coordinator import (
    AgentWorkflowCoordinator,
    ShadowWorkflowRequest,
    ShadowWorkflowResult,
    direct_shadow_directive,
)
from slim_guard.orchestration.evidence import EvidenceBuilder
from slim_guard.tools.contracts import (
    ToolContext,
    ToolExecution,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolResult,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def text_response(text: str, *, tokens: int = 0) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        usage=ModelUsage(total_tokens=tokens),
    )


def write_response() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id="test-write", name="record_weight", arguments={"weight_kg": 77.6}
                ),
            ),
        )
    )


class Initializer:
    async def initialize(self, request: TurnInitializationRequest) -> InitializedTurn:
        return InitializedTurn(
            thread=ThreadRef(id="test-thread", user_id=request.user_id, status=ThreadStatus.ACTIVE),
            turn=TurnRef(
                id="test-turn",
                thread_id="test-thread",
                agent_version_id="test-version",
                trigger=TurnTrigger.USER_MESSAGE,
                status=TurnStatus.RUNNING,
                deadline_at=request.deadline_at,
                completed_at=None,
            ),
            input_items=tuple(
                ItemRef(
                    id=f"test-input-{index}",
                    turn_id="test-turn",
                    sequence=index,
                    item_type=item.item_type,
                    status=ItemStatus.COMPLETED,
                    payload=item.payload,
                )
                for index, item in enumerate(request.inputs)
            ),
            context=HarnessTurnContext(
                thread_id="test-thread",
                turn_id="test-turn",
                user_id=request.user_id,
                agent_version_id="test-version",
                execution_mode=ToolExecutionMode.EVALUATION,
                deadline_at=request.deadline_at,
            ),
            source_item_id="test-input-0",
        )


class Compiler:
    def compile(
        self,
        *,
        initialized: InitializedTurn,
        allowed_tool_names=None,
        authoritative_context=None,
        **kwargs,
    ) -> CompiledContext:
        names = ("record_weight",) if allowed_tool_names is None else allowed_tool_names
        return CompiledContext(
            request=ModelRequest(
                purpose=ModelPurpose.HARNESS_TURN,
                model="test-model",
                messages=(
                    ModelMessage(role=MessageRole.SYSTEM, content="Test system"),
                    ModelMessage(
                        role=MessageRole.SYSTEM, content=json.dumps(authoritative_context)
                    ),
                    ModelMessage(
                        role=MessageRole.USER, content=initialized.input_items[0].payload["text"]
                    ),
                ),
                tools=tuple(
                    ToolDefinition(
                        name=name,
                        description="Test tool",
                        parameters_json_schema={"type": "object"},
                        version="test-v1",
                    )
                    for name in names
                ),
            ),
            allowed_tool_names=names,
            input_item_ids=("test-input-0",),
            evidence_item_ids=("test-input-0",),
        )


class Tools:
    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed
        self.calls = 0

    async def execute(self, *, call: NormalizedToolCall, context: ToolContext, **kwargs):
        self.calls += 1
        return ToolCallOutcome(
            execution=ToolExecution(
                tool_call_id=call.id,
                tool_name=call.name,
                tool_version="test-v1",
                canonical_arguments=call.arguments,
                idempotency_key="test-once",
                policy_decision=ToolPolicyDecision.ALLOW,
                result=ToolResult.failed(
                    code="test_write_failure", message="Test write failed", retryable=False
                )
                if self.failed
                else ToolResult.success(
                    output={"weight_kg": 77.6}, source_ids=("test-weight-record",)
                ),
            ),
            turn=TurnRef(
                id=context.turn_id,
                thread_id=context.thread_id,
                agent_version_id=context.agent_version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                status=TurnStatus.RUNNING,
                deadline_at=None,
                completed_at=None,
            ),
            pending_action=None,
        )


class ContextData:
    def __init__(self, tools: Tools) -> None:
        self.tools = tools
        self.calls = 0

    async def load(self, **kwargs) -> dict[str, Any]:
        self.calls += 1
        return {
            "recent_weights": [
                {
                    "id": "test-weight-record",
                    "weight_kg": 77.6 if self.tools.calls and not self.tools.failed else 80.0,
                }
            ]
        }


class Recorder(NullHarnessRunRecorder):
    def __init__(self) -> None:
        self.events: list[tuple[ItemType, dict[str, Any]]] = []
        self.finals: list[dict[str, Any]] = []

    async def record_workflow_event(self, *, event_type, payload, **kwargs) -> None:
        self.events.append((ItemType(event_type), dict(payload)))

    async def finish_run(self, **kwargs) -> None:
        self.finals.append(kwargs)


class Workflow:
    def __init__(
        self,
        *,
        candidate: str = "收到！",
        fail: bool = False,
        stall: bool = False,
        reviewed: bool = True,
        stale_verdict: bool = False,
    ) -> None:
        self.candidate = candidate
        self.fail = fail
        self.stall = stall
        self.reviewed = reviewed
        self.stale_verdict = stale_verdict
        self.requests: list[ShadowWorkflowRequest] = []

    async def run_shadow(self, request: ShadowWorkflowRequest) -> ShadowWorkflowResult:
        self.requests.append(request)
        if self.stall:
            await asyncio.Event().wait()
        if self.fail:
            raise RuntimeError("test workflow failure")
        candidate = AgentArtifact.create(
            artifact_id="test-style",
            turn_id=request.turn_id,
            producer_role=ArtifactProducerRole.RESPONSE_STYLE,
            artifact_type="styled_response",
            schema_version="1",
            payload={"text": self.candidate},
            created_at=NOW,
        )
        verdict = AgentArtifact.create(
            artifact_id="test-review",
            turn_id=request.turn_id,
            producer_role=ArtifactProducerRole.RESPONSE_REVIEWER,
            artifact_type="reviewer_verdict",
            schema_version="1",
            payload={
                "verdict": "pass",
                "reviewed_artifact_ids": [
                    "old-test-style" if self.stale_verdict else candidate.artifact_id
                ],
            },
            parent_artifact_ids=(candidate.artifact_id,),
            created_at=NOW,
        )
        return ShadowWorkflowResult(
            status=InvocationStatus.SUCCEEDED,
            shadow_candidate=self.candidate,
            legacy_response=request.legacy_response,
            actual_nodes=(),
            invocations=(),
            artifacts=(candidate, verdict) if self.reviewed else (candidate,),
            transitions=(),
            model_call_count=1,
            total_token_count=10,
        )


def setup(*, steps, workflow: Workflow, failed_write: bool = False, limits=None):
    tools = Tools(failed=failed_write)
    data = ContextData(tools)
    recorder = Recorder()
    model = ScriptedModelGateway(steps)
    runner = HarnessTurnRunner(
        initializer=Initializer(),
        compiler=Compiler(),
        model=model,
        tool_calls=tools,
        recorder=recorder,
        limits=limits or HarnessLimits(),
        context_data=data,
        output_guard=SlimGuardOutputGuard(),
        shadow_workflow=workflow,
        workflow_mode="on",
        workflow_adopts_for=lambda _: True,
        workflow_timeout_seconds=0.01,
        clock=lambda: NOW,
    )
    return runner, tools, data, recorder, model


def turn_request(text: str = "测试：今天77.6kg") -> TurnInitializationRequest:
    return TurnInitializationRequest(
        user_id="test-user",
        agent_version_id="test-version",
        trigger=TurnTrigger.USER_MESSAGE,
        execution_mode=ToolExecutionMode.EVALUATION,
        inputs=(TurnInput.user_message(text=text),),
        deadline_at=NOW + timedelta(seconds=30),
    )


@pytest.mark.parametrize("failure", ["exception", "timeout", "no_review"])
async def test_workflow_failure_keeps_guarded_legacy_without_reexecuting_business_write(failure):
    baseline = "已记录今天的体重77.6kg。"
    workflow = Workflow(
        fail=failure == "exception", stall=failure == "timeout", reviewed=failure != "no_review"
    )
    runner, tools, _, recorder, model = setup(
        steps=[write_response(), text_response(baseline)], workflow=workflow
    )
    result = await runner.run(request=turn_request())
    assert result.final_text == baseline
    assert tools.calls == 1
    assert len(recorder.finals) == 1
    assert recorder.finals[0]["final_text"] == baseline
    assert not any(event is ItemType.RESPONSE_ADOPTED for event, _ in recorder.events)
    model.assert_exhausted()


async def test_candidate_output_guard_uses_actual_failed_write_receipt():
    baseline = "这次没有写入成功，请稍后再试。"
    workflow = Workflow(candidate="已记录今天的体重77.6kg。")
    runner, tools, _, recorder, _ = setup(
        steps=[write_response(), text_response(baseline)], workflow=workflow, failed_write=True
    )
    result = await runner.run(request=turn_request())
    assert result.final_text == baseline
    assert tools.calls == 1
    assert len(workflow.requests) == 1
    assert not any(event is ItemType.RESPONSE_ADOPTED for event, _ in recorder.events)


async def test_unsafe_baseline_bypasses_workflow_and_preserves_safety_replacement():
    workflow = Workflow()
    runner, tools, _, _, _ = setup(steps=[text_response("你患有糖尿病。")], workflow=workflow)
    result = await runner.run(request=turn_request())
    assert "不能提供疾病诊断" in result.final_text
    assert workflow.requests == []
    assert tools.calls == 0


async def test_emergency_bypasses_entire_candidate_graph_and_removes_tools():
    workflow = Workflow()
    runner, tools, _, _, model = setup(steps=[text_response("收到。")], workflow=workflow)
    result = await runner.run(request=turn_request("测试：我现在胸痛，呼吸困难"))
    assert "急诊" in result.final_text
    assert not model.requests[0].tools
    assert workflow.requests == []
    assert tools.calls == 0


async def test_live_evidence_contains_structured_receipts_and_fresh_committed_records():
    workflow = Workflow(candidate="已记录今天的体重77.6kg！")
    runner, tools, data, _, _ = setup(
        steps=[write_response(), text_response("已记录今天的体重77.6kg。")], workflow=workflow
    )
    await runner.run(request=turn_request())
    request = workflow.requests[0]
    assert data.calls >= 2, "Live professional reasoning must reload records after a business write"
    assert request.authoritative_context["recent_weights"][0]["weight_kg"] == 77.6
    packet = await EvidenceBuilder().build(
        turn_id=request.turn_id,
        user_request=request.user_request,
        professional_question="测试核对当前记录",
        current_items=request.current_items,
        authoritative_context=request.authoritative_context,
    )
    receipts = [item for item in packet.items if item.source_type.value == "tool_receipt"]
    assert len(receipts) == 1
    assert receipts[0].content["status"] == "succeeded"
    assert receipts[0].content["output"]["weight_kg"] == 77.6
    assert tools.calls == 1


async def test_refreshed_snapshot_does_not_resurrect_removed_memory_keys(monkeypatch):
    workflow = Workflow()
    runner, _, data, _, _ = setup(steps=[text_response("收到。")], workflow=workflow)
    snapshots = [
        {"profile_memory": [{"memory_id": "test-deleted-memory", "value": "测试：旧偏好"}]},
        {},
    ]

    async def load(**kwargs):
        return snapshots.pop(0)

    monkeypatch.setattr(data, "load", load)
    await runner.run(request=turn_request())
    assert "profile_memory" not in workflow.requests[0].authoritative_context
    assert all(
        "test-deleted-memory" not in (message.content or "")
        for message in workflow.requests[0].context
    )


@pytest.mark.parametrize("budget", ["model_calls", "tokens"])
async def test_exhausted_turn_budget_does_not_start_additional_candidate_model_calls(budget):
    workflow = Workflow()
    runner, _, _, _, _ = setup(
        steps=[text_response("收到。", tokens=10)],
        workflow=workflow,
        limits=HarnessLimits(
            max_model_calls=1 if budget == "model_calls" else 6,
            max_total_tokens=10 if budget == "tokens" else 1000,
        ),
    )
    result = await runner.run(request=turn_request())
    assert result.final_text == "收到。"
    assert workflow.requests == []


async def test_stale_passing_verdict_cannot_approve_a_different_final_artifact():
    workflow = Workflow(stale_verdict=True)
    runner, _, _, recorder, _ = setup(steps=[text_response("收到。")], workflow=workflow)
    result = await runner.run(request=turn_request())
    assert result.final_text == "收到。"
    assert not any(event is ItemType.RESPONSE_ADOPTED for event, _ in recorder.events)


async def test_adopted_candidate_usage_is_included_in_final_turn_totals():
    workflow = Workflow()
    runner, _, _, recorder, _ = setup(steps=[text_response("收到。", tokens=7)], workflow=workflow)
    result = await runner.run(request=turn_request())
    assert result.final_text == "收到！"
    assert result.loop.model_call_count == 2
    assert result.loop.total_token_count == 17
    assert recorder.finals[0]["model_call_count"] == 2
    assert recorder.finals[0]["total_token_count"] == 17


class GraphGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        # Force interleaving to exercise budget isolation between concurrent turns.
        await asyncio.sleep(0)
        if request.purpose is ModelPurpose.ORCHESTRATOR:
            content = direct_shadow_directive("测试：收到。").model_dump_json()
        elif request.purpose is ModelPurpose.RESPONSE_STYLE:
            raw = json.loads(request.messages[-1].content or "{}")
            context = StyleContext.model_validate(raw.get("style_context", raw))
            content = NeutralRenderer().render(context).model_dump_json()
        else:
            assert request.purpose is ModelPurpose.RESPONSE_REVIEWER
            content = '{"verdict":"pass"}'
        return text_response(content, tokens=7)

    async def close(self) -> None:
        pass


def graph_request(*, max_calls: int = 3, max_tokens: int = 100) -> ShadowWorkflowRequest:
    return ShadowWorkflowRequest(
        trace_id="test-trace",
        turn_id=f"test-turn-budget-{max_calls}",
        context=(ModelMessage(role=MessageRole.USER, content="测试：你好。"),),
        user_request="测试：你好。",
        legacy_response="测试：收到。",
        mode="on",
        max_model_calls=max_calls,
        max_total_tokens=max_tokens,
        deadline_at=NOW + timedelta(seconds=30),
    )


def coordinator(gateway: GraphGateway) -> AgentWorkflowCoordinator:
    return AgentWorkflowCoordinator(
        model=gateway,
        recorder=NullHarnessRunRecorder(),
        model_name="test-model",
        graph_version="test-live",
        reviewer_enabled=True,
        clock=lambda: NOW,
    )


async def test_graph_aggregate_budget_is_shared_by_orchestrator_style_and_reviewer():
    gateway = GraphGateway()
    result = await coordinator(gateway).run_shadow(graph_request(max_calls=2))
    assert result.status is not InvocationStatus.SUCCEEDED
    assert result.model_call_count == 2
    assert result.total_token_count == 14
    assert [request.purpose for request in gateway.requests] == [
        ModelPurpose.ORCHESTRATOR,
        ModelPurpose.RESPONSE_STYLE,
    ]


async def test_graph_token_overrun_fails_closed_and_does_not_start_reviewer():
    gateway = GraphGateway()
    result = await coordinator(gateway).run_shadow(graph_request(max_tokens=10))
    assert result.status is not InvocationStatus.SUCCEEDED
    assert result.total_token_count == 14
    assert len(gateway.requests) == 2
    assert gateway.requests[-1].max_output_tokens <= 3


async def test_concurrent_live_turns_do_not_share_or_leak_aggregate_budgets():
    gateway = GraphGateway()
    workflow = coordinator(gateway)
    low_budget, full_budget = await asyncio.gather(
        workflow.run_shadow(graph_request(max_calls=1)),
        workflow.run_shadow(graph_request(max_calls=3)),
    )
    assert low_budget.status is not InvocationStatus.SUCCEEDED
    assert low_budget.model_call_count == 1
    assert full_budget.status is InvocationStatus.SUCCEEDED
    assert full_budget.model_call_count == 3
    assert full_budget.total_token_count == 21
    assert len(gateway.requests) == 4
