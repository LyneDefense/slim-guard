from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.contracts import (
    AgentInvocation,
    AgentRole,
    InvocationStatus,
    ProfessionalAssessment,
    ProfessionalClaim,
    TurnDirective,
)
from slim_guard.agents.structured_runner import StructuredAgentRunner
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.orchestration.coordinator import (
    AgentWorkflowCoordinator,
    ShadowWorkflowRequest,
    direct_shadow_directive,
)
from slim_guard.orchestration.evidence import EvidenceBuilder

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)


def invocation(*, max_model_calls: int = 2, deadline: datetime | None = None) -> AgentInvocation:
    return AgentInvocation(
        invocation_id="invocation-1",
        trace_id="trace-1",
        turn_id="turn-1",
        graph_version="typed-supervisor-v1",
        agent_role=AgentRole.ORCHESTRATOR,
        agent_version="orchestrator-v1",
        privacy_scopes=("current_user_message",),
        deadline_at=deadline or NOW + timedelta(seconds=20),
        max_model_calls=max_model_calls,
        max_tool_calls=0,
        max_total_tokens=100,
    )


def request() -> ModelRequest:
    return ModelRequest(
        purpose=ModelPurpose.ORCHESTRATOR,
        model="glm-5.2",
        messages=(ModelMessage(role=MessageRole.USER, content="你好"),),
        tools=(),
        tool_choice=ToolChoice.NONE,
        response_format=ResponseFormat.JSON_OBJECT,
        output_schema_name="TurnDirective",
    )


def response(content: str, *, tokens: int = 10) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=content),
        usage=ModelUsage(total_tokens=tokens),
    )


async def test_structured_runner_repairs_invalid_json_once() -> None:
    directive = direct_shadow_directive("收到，我在。")
    model = ScriptedModelGateway((response("not json"), response(directive.model_dump_json())))
    runner = StructuredAgentRunner(model=model, clock=lambda: NOW)

    result = await runner.run(
        invocation=invocation(),
        request=request(),
        output_type=TurnDirective,
    )

    assert result.status is InvocationStatus.SUCCEEDED
    assert result.output == directive
    assert result.model_call_count == 2
    assert "previous output was invalid" in (model.requests[1].messages[-1].content or "")


async def test_structured_runner_rejects_tool_calls_and_expired_deadline() -> None:
    tool_response = ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(NormalizedToolCall(id="call-1", name="record_weight", arguments={}),),
        )
    )
    model = ScriptedModelGateway((tool_response,))
    runner = StructuredAgentRunner(model=model, clock=lambda: NOW)

    unexpected_tool = await runner.run(
        invocation=invocation(),
        request=request(),
        output_type=TurnDirective,
    )
    expired = await runner.run(
        invocation=invocation(deadline=NOW - timedelta(seconds=1)),
        request=request(),
        output_type=TurnDirective,
    )

    assert unexpected_tool.failure_code == "unexpected_tool_call"
    assert expired.failure_code == "deadline_exceeded"
    assert len(model.requests) == 1


async def test_shadow_coordinator_returns_unadopted_no_write_candidate() -> None:
    directive = direct_shadow_directive("收到，先按现在的节奏继续。")
    styled = {
        "schema_version": "1",
        "text": "收到，先按现在的节奏继续。",
        "used_block_ids": ["direct-response"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "slimguard_default_v1",
    }
    model = ScriptedModelGateway(
        (response(directive.model_dump_json()), response(json.dumps(styled)))
    )
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=NullHarnessRunRecorder(),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        clock=lambda: NOW,
    )

    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace-1",
            turn_id="turn-1",
            thread_id="thread-1",
            context=(ModelMessage(role=MessageRole.USER, content="今天完成打卡"),),
            legacy_response="已记录。",
            deadline_at=NOW + timedelta(seconds=30),
        )
    )

    assert result.status is InvocationStatus.SUCCEEDED
    assert result.shadow_candidate == "收到，先按现在的节奏继续。"
    assert result.legacy_response == "已记录。"
    assert result.delivered is False
    assert result.business_write_count == 0
    assert [artifact.artifact_type for artifact in result.artifacts] == [
        "directive",
        "response_plan",
        "style_resolution",
        "styled_response",
    ]
    assert all(invocation.allowed_tools == () for invocation in result.invocations)


async def test_shadow_coordinator_contains_model_failure() -> None:
    model = ScriptedModelGateway((response("bad"), response("still bad")))
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=NullHarnessRunRecorder(),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        clock=lambda: NOW,
    )

    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace-1",
            turn_id="turn-1",
            context=(ModelMessage(role=MessageRole.USER, content="你好"),),
            deadline_at=NOW + timedelta(seconds=30),
        )
    )

    assert result.status is InvocationStatus.FAILED
    assert result.shadow_candidate is None
    assert result.failure_code == "structured_output_invalid"


async def test_shadow_coordinator_can_bypass_style_with_neutral_rendering() -> None:
    directive = direct_shadow_directive("保留原始内容。")
    model = ScriptedModelGateway((response(directive.model_dump_json()),))
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=NullHarnessRunRecorder(),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        style_enabled=False,
        clock=lambda: NOW,
    )

    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace-1",
            turn_id="turn-1",
            context=(ModelMessage(role=MessageRole.USER, content="你好"),),
            deadline_at=NOW + timedelta(seconds=30),
        )
    )

    assert result.status is InvocationStatus.SUCCEEDED
    assert result.shadow_candidate == "保留原始内容。"
    assert [item.agent_role.value for item in result.invocations] == ["orchestrator"]
    assert [item.artifact_type for item in result.artifacts] == [
        "directive",
        "response_plan",
        "neutral_response",
    ]
    assert result.transitions[-1].reason.value == "style_bypassed"


async def test_shadow_coordinator_runs_evidence_nutrition_and_style_path() -> None:
    authoritative_context = {
        "recent_weights": [
            {
                "record_id": "weight-record-1",
                "weight_kg": 77.6,
                "measured_at": "2026-09-05T08:00:00+00:00",
                "status": "active",
            }
        ]
    }
    routing_packet = await EvidenceBuilder().build(
        turn_id="turn-1",
        user_request="看看我现在的体重情况",
        professional_question="判断本轮是否需要营养专业分析",
        current_items=(),
        authoritative_context=authoritative_context,
    )
    evidence_id = routing_packet.items[0].evidence_id
    directive = TurnDirective(
        response_path="professional_assessment",
        interaction_kind="review",
        user_need_summary="分析当前体重记录",
        response_brief="基于当前记录做专业分析",
        evidence_refs=(evidence_id,),
        professional_question="当前体重记录说明什么？",
        voice_act="explain",
    )
    assessment = ProfessionalAssessment(
        assessment_type="progress",
        overall="目前只有一次体重记录，不能判断长期趋势。",
        findings=(
            ProfessionalClaim(
                claim_id="claim-current-weight",
                category="current_weight",
                statement="当前可核对的体重记录是 77.6kg。",
                basis_types=("user_evidence",),
                evidence_refs=(evidence_id,),
                confidence="high",
            ),
        ),
        uncertainty_note="还需要连续记录才能判断变化趋势。",
    )
    styled = {
        "schema_version": "1",
        "text": (
            "目前只有一次体重记录，不能判断长期趋势。\n"
            "当前可核对的体重记录是 77.6kg。\n"
            "还需要连续记录才能判断变化趋势。"
        ),
        "used_block_ids": [
            "assessment-overall",
            "finding-1",
            "assessment-uncertainty",
        ],
        "used_claim_ids": ["claim-current-weight"],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "slimguard_default_v1",
    }
    model = ScriptedModelGateway(
        (
            response(directive.model_dump_json()),
            response(assessment.model_dump_json()),
            response(json.dumps(styled, ensure_ascii=False)),
        )
    )
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=NullHarnessRunRecorder(),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        nutrition_enabled=True,
        clock=lambda: NOW,
    )

    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace-1",
            turn_id="turn-1",
            context=(ModelMessage(role=MessageRole.USER, content="看看体重"),),
            user_request="看看我现在的体重情况",
            authoritative_context=authoritative_context,
            legacy_response="线上回复保持不变。",
            deadline_at=NOW + timedelta(seconds=30),
        )
    )

    assert result.status is InvocationStatus.SUCCEEDED
    assert result.legacy_response == "线上回复保持不变。"
    assert result.delivered is False
    assert result.business_write_count == 0
    assert [item.agent_role.value for item in result.invocations] == [
        "orchestrator",
        "nutrition_expert",
        "response_style",
    ]
    assert all(item.allowed_tools == () for item in result.invocations)
    assert [item.artifact_type for item in result.artifacts] == [
        "directive",
        "evidence_packet",
        "nutrition_observations",
        "professional_assessment",
        "response_plan",
        "style_resolution",
        "styled_response",
    ]
    assert result.actual_nodes == (
        "orchestrator_running",
        "evidence_ready",
        "expert_running",
        "style_resolved",
        "style_running",
        "output_guarded",
    )
    observations = next(
        item for item in result.artifacts if item.artifact_type == "nutrition_observations"
    )
    assert observations.payload["knowledge"]["corpus_status"] == "empty"
    assert observations.payload["tool_receipts"][-1]["output"]["citations"] == []
    orchestrator_content = model.requests[0].messages[-1].content or "{}"
    orchestrator_payload = json.loads(orchestrator_content.split("\n", maxsplit=1)[-1])
    assert orchestrator_payload["evidence_catalog"][0]["evidence_id"] == evidence_id


async def test_shadow_nutrition_failure_degrades_without_replacing_legacy_reply() -> None:
    current_items = (
        {
            "id": "message-1",
            "item_type": "user_message",
            "payload": {"text": "帮我分析这顿饭"},
        },
    )
    routing_packet = await EvidenceBuilder().build(
        turn_id="turn-1",
        user_request="帮我分析这顿饭",
        professional_question="判断本轮是否需要营养专业分析",
        current_items=current_items,
        authoritative_context={},
    )
    directive = TurnDirective(
        response_path="professional_assessment",
        interaction_kind="question",
        user_need_summary="分析饮食",
        response_brief="分析当前信息",
        evidence_refs=(routing_packet.items[0].evidence_id,),
        professional_question="这顿饭如何？",
        voice_act="explain",
    )
    model = ScriptedModelGateway(
        (
            response(directive.model_dump_json()),
            response("invalid nutrition"),
            response("still invalid"),
        )
    )
    coordinator = AgentWorkflowCoordinator(
        model=model,
        recorder=NullHarnessRunRecorder(),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        nutrition_enabled=True,
        style_enabled=False,
        clock=lambda: NOW,
    )

    result = await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace-1",
            turn_id="turn-1",
            context=(ModelMessage(role=MessageRole.USER, content="帮我分析这顿饭"),),
            user_request="帮我分析这顿饭",
            current_items=current_items,
            legacy_response="线上记录确认照常完成。",
            deadline_at=NOW + timedelta(seconds=30),
        )
    )

    assert result.status is InvocationStatus.DEGRADED
    assert result.legacy_response == "线上记录确认照常完成。"
    assert result.shadow_candidate is not None
    assert "证据不足" in result.shadow_candidate
    assert result.delivered is False
    assert result.business_write_count == 0
    assert "conservative_assessment" in {
        artifact.artifact_type for artifact in result.artifacts
    }
    assert result.transitions[-2].reason.value == "insufficient_evidence"
    assert result.transitions[-1].reason.value == "style_bypassed"
