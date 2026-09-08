from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.errors import ModelTimeoutError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelResponse,
    ModelUsage,
    ToolChoice,
)
from slim_guard.agents.contracts import (
    AgentInvocation,
    AgentRole,
    InvocationStatus,
    ProfessionalAssessment,
    ProfessionalClaim,
    ResponseContentBlock,
    ResponsePlan,
    ReviewerVerdict,
    StyledResponse,
    TurnDirective,
)
from slim_guard.agents.reviewer import (
    ResponseReviewerAgent,
    ReviewerContext,
    ReviewerContextCompiler,
    ReviewerEvidenceSummary,
    ReviewerVerdictValidator,
)
from slim_guard.agents.structured_runner import StructuredAgentRunner
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.orchestration.graph import InvocationGrant


def invocation(**updates: object) -> AgentInvocation:
    return AgentInvocation.model_validate(
        {
            "invocation_id": "review-1",
            "trace_id": "trace-1",
            "turn_id": "turn-1",
            "graph_version": "typed-supervisor-v1",
            "agent_role": "response_reviewer",
            "agent_version": "review-v1",
            "deadline_at": datetime.now(UTC) + timedelta(seconds=30),
            "max_model_calls": 2,
            "max_tool_calls": 0,
            "max_total_tokens": 4096,
            **updates,
        }
    )


def context() -> ReviewerContext:
    return ReviewerContextCompiler().compile(
        turn_id="turn-1",
        response_plan=ResponsePlan(
            communication_act="explain",
            content_blocks=(
                ResponseContentBlock(
                    block_id="weight-block",
                    kind="claim",
                    text="本次记录体重 77.6kg，尚不能判断长期趋势。",
                    source_refs=("weight-claim",),
                ),
            ),
        ),
        styled_response=StyledResponse(
            text="本次记录体重 77.6kg，尚不能判断长期趋势。",
            used_block_ids=("weight-block",),
            used_claim_ids=("weight-claim",),
            style_profile_version=SLIMGUARD_DEFAULT_V1.version,
        ),
        assessment=ProfessionalAssessment(
            assessment_type="progress",
            overall="已有单次体重记录。",
            findings=(
                ProfessionalClaim(
                    claim_id="weight-claim",
                    category="weight",
                    statement="本次记录体重 77.6kg，尚不能判断长期趋势。",
                    basis_types=("user_evidence",),
                    evidence_refs=("weight-1",),
                    confidence="high",
                ),
            ),
            uncertainty_note="尚缺连续体重数据。",
        ),
        directive=TurnDirective(
            response_path="professional_assessment",
            interaction_kind="review",
            user_need_summary="了解近期体重趋势。",
            response_brief="根据记录解释趋势及数据局限。",
            professional_question="近期体重趋势如何？",
            evidence_refs=("weight-1",),
            voice_act="explain",
        ),
        available_evidence_ids=("weight-1",),
        evidence_summaries=(
            ReviewerEvidenceSummary(evidence_id="weight-1", summary="仅有单次体重：77.6kg。"),
        ),
    )


def response(content: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=content),
        usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
    )


def agent(gateway: ScriptedModelGateway) -> ResponseReviewerAgent:
    return ResponseReviewerAgent(runner=StructuredAgentRunner(model=gateway), model="test-model")


async def test_pass_receives_minimal_facts_and_directive_without_tools() -> None:
    gateway = ScriptedModelGateway([response('{"verdict":"pass"}')])
    result = await agent(gateway).run(invocation=invocation(), context=context())
    assert result.status is InvocationStatus.SUCCEEDED
    assert result.verdict.verdict == "pass"
    assert not result.used_fallback
    assert result.model_call_count == 1
    assert result.total_token_count == 30
    request = gateway.requests[0]
    assert request.purpose is ModelPurpose.RESPONSE_REVIEWER
    assert request.tools == ()
    assert request.tool_choice is ToolChoice.NONE
    data = json.loads(request.messages[1].content or "{}")
    assert data["evidence_summaries"] == [
        {"evidence_id": "weight-1", "summary": "仅有单次体重：77.6kg。"}
    ]
    assert data["directive"]["professional_question"] == "近期体重趋势如何？"
    assert "payload" not in data


@pytest.mark.parametrize(
    ("issue", "target"),
    [
        ("changed_uncertainty", "response_style"),
        ("unsupported_professional_claim", "nutrition_expert"),
        ("missing_user_evidence", "orchestrator"),
    ],
)
async def test_semantic_issues_reach_only_their_repair_target(issue: str, target: str) -> None:
    gateway = ScriptedModelGateway(
        [
            response(
                json.dumps(
                    {
                        "verdict": "repair",
                        "issue_type": issue,
                        "repair_target": target,
                        "reason_summary": "现有内容需要按指定职责修正。",
                    }
                )
            )
        ]
    )
    review_context = context()
    if target == "orchestrator":
        review_context = review_context.model_copy(
            update={"available_evidence_ids": (), "evidence_summaries": ()}
        )
    result = await agent(gateway).run(invocation=invocation(), context=review_context)
    assert result.status is InvocationStatus.SUCCEEDED
    assert result.verdict.verdict == "repair"
    assert result.verdict.repair_target == target
    assert result.model_call_count == 1


async def test_invalid_schema_gets_one_repair_with_original_context() -> None:
    gateway = ScriptedModelGateway([response("not json"), response('{"verdict":"pass"}')])
    result = await agent(gateway).run(invocation=invocation(), context=context())
    assert result.verdict.verdict == "pass"
    assert result.repair_attempted
    assert result.model_call_count == 2
    assert result.total_token_count == 60
    assert gateway.requests[1].messages[:2] == gateway.requests[0].messages
    assert "ReviewerVerdict" in (gateway.requests[1].messages[-1].content or "")


@pytest.mark.parametrize("max_calls,expected_calls", [(1, 1), (2, 2), (10, 2)])
async def test_failed_schema_repair_rejects_with_a_hard_two_call_cap(
    max_calls: int,
    expected_calls: int,
) -> None:
    gateway = ScriptedModelGateway([response("invalid")] * 3)
    result = await agent(gateway).run(
        invocation=invocation(max_model_calls=max_calls), context=context()
    )
    assert result.status is InvocationStatus.DEGRADED
    assert result.verdict.verdict == "reject"
    assert result.verdict.repair_target is None
    assert result.used_fallback
    assert result.failure_code == "structured_output_invalid"
    assert result.model_call_count == expected_calls
    assert len(gateway.requests) == expected_calls


async def test_model_failure_conservatively_rejects() -> None:
    gateway = ScriptedModelGateway([ModelTimeoutError("unavailable")])
    result = await agent(gateway).run(invocation=invocation(), context=context())
    assert result.verdict.verdict == "reject"
    assert result.failure_code == "model_gateway_error"
    assert result.used_fallback


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"agent_role": "nutrition_expert"}, "reviewer_invocation_role_mismatch"),
        ({"turn_id": "wrong-turn"}, "reviewer_context_turn_mismatch"),
        ({"allowed_tools": ("read_memory",)}, "reviewer_tools_not_allowed"),
        ({"max_tool_calls": 1}, "reviewer_tools_not_allowed"),
    ],
)
async def test_invalid_invocation_never_calls_the_model(
    updates: dict[str, object],
    code: str,
) -> None:
    gateway = ScriptedModelGateway([])
    result = await agent(gateway).run(invocation=invocation(**updates), context=context())
    assert result.failure_code == code
    assert result.verdict.verdict == "reject"
    assert gateway.requests == []


async def test_invocation_cannot_escalate_privacy_grant() -> None:
    gateway = ScriptedModelGateway([])
    result = await agent(gateway).run(
        invocation=invocation(privacy_scopes=("raw_database",)),
        context=context(),
        grant=InvocationGrant(agent_role=AgentRole.RESPONSE_REVIEWER, max_tool_calls=0),
    )
    assert result.failure_code == "reviewer_invocation_unauthorized"
    assert result.verdict.verdict == "reject"
    assert gateway.requests == []


@pytest.mark.parametrize(
    "updates,issue",
    [
        ({"used_block_ids": ()}, "omitted_required_content"),
        ({"used_block_ids": ("weight-block", "invented")}, "changed_meaning"),
        ({"used_claim_ids": ()}, "omitted_required_content"),
        ({"used_claim_ids": ("weight-claim", "invented")}, "unsupported_claim"),
        ({"used_action_ids": ("invented",)}, "unsupported_claim"),
        ({"preserved_citation_refs": ("invented",)}, "unsupported_claim"),
        ({"preserved_risk_flags": ("invented",)}, "unsupported_claim"),
        ({"style_profile_version": "wrong"}, "style_drift"),
    ],
)
async def test_false_pass_on_reference_mismatch_rejects_without_semantic_retry(
    updates: dict[str, object],
    issue: str,
) -> None:
    review_context = context()
    review_context = review_context.model_copy(
        update={"styled_response": review_context.styled_response.model_copy(update=updates)}
    )
    gateway = ScriptedModelGateway([response('{"verdict":"pass"}')])
    result = await agent(gateway).run(invocation=invocation(), context=review_context)
    assert result.verdict.verdict == "reject"
    assert result.failure_code == "reviewer_integrity_invalid"
    assert issue in result.validation_report.detected_issue_types
    assert len(gateway.requests) == 1


def test_unknown_references_without_assessment_are_not_implicitly_trusted() -> None:
    review_context = context().model_copy(update={"assessment": None})
    styled = review_context.styled_response.model_copy(update={"used_action_ids": ("fake",)})
    report = ReviewerVerdictValidator().validate(
        review_context.model_copy(update={"styled_response": styled}),
        ReviewerVerdict(verdict="pass"),
    )
    assert "unsupported_claim" in report.detected_issue_types


def test_directive_missing_evidence_is_checked_without_assessment() -> None:
    review_context = context().model_copy(
        update={
            "assessment": None,
            "available_evidence_ids": (),
            "evidence_summaries": (),
        }
    )
    report = ReviewerVerdictValidator().validate(review_context, ReviewerVerdict(verdict="pass"))
    assert "missing_user_evidence" in report.detected_issue_types


def test_unsupplied_evidence_catalog_does_not_imply_missing_evidence() -> None:
    review_context = context().model_copy(
        update={
            "available_evidence_ids": None,
            "evidence_summaries": (),
        }
    )
    assert (
        ReviewerVerdictValidator()
        .validate(review_context, ReviewerVerdict(verdict="pass"))
        .is_valid
    )


@pytest.mark.parametrize("summary_ids", [("weight-1", "weight-1"), ("unknown",)])
def test_evidence_summaries_cannot_duplicate_or_invent_available_ids(
    summary_ids: tuple[str, ...],
) -> None:
    payload = context().model_dump()
    payload["evidence_summaries"] = [
        {"evidence_id": evidence_id, "summary": "事实摘要"} for evidence_id in summary_ids
    ]
    with pytest.raises(ValidationError):
        ReviewerContext.model_validate(payload)


async def test_inconsistent_pass_reason_is_rejected() -> None:
    gateway = ScriptedModelGateway([response('{"verdict":"pass","reason_summary":"有问题"}')])
    result = await agent(gateway).run(invocation=invocation(), context=context())
    assert result.verdict.verdict == "reject"
    assert result.failure_code == "reviewer_integrity_invalid"
    assert "pass_reason_present" in result.validation_report.issue_codes


def test_omitted_required_citation_and_risk_prevent_pass() -> None:
    review_context = context()
    assert review_context.assessment is not None
    review_context = review_context.model_copy(
        update={
            "response_plan": review_context.response_plan.model_copy(
                update={"citation_refs": ("citation-1",)}
            ),
            "assessment": review_context.assessment.model_copy(
                update={"risk_flags": ("seek_professional_help",)}
            ),
        }
    )
    report = ReviewerVerdictValidator().validate(review_context, ReviewerVerdict(verdict="pass"))
    assert "omitted_required_content" in report.detected_issue_types
    preserved = review_context.styled_response.model_copy(
        update={
            "preserved_risk_flags": ("seek_professional_help",),
            "preserved_citation_refs": ("citation-1",),
        }
    )
    assert (
        ReviewerVerdictValidator()
        .validate(
            review_context.model_copy(update={"styled_response": preserved}),
            ReviewerVerdict(verdict="pass"),
        )
        .is_valid
    )


async def test_wrong_repair_direction_is_schema_repaired_before_being_accepted() -> None:
    verdict = {
        "verdict": "repair",
        "issue_type": "medical_overreach",
        "repair_target": "orchestrator",
        "reason_summary": "不能根据单次体重记录作出诊断。",
    }
    gateway = ScriptedModelGateway(
        [
            response(json.dumps(verdict)),
            response(json.dumps({**verdict, "repair_target": "nutrition_expert"})),
        ]
    )
    result = await agent(gateway).run(invocation=invocation(), context=context())
    assert result.verdict.repair_target == "nutrition_expert"
    assert result.repair_attempted
    assert result.model_call_count == 2


async def test_expired_invocation_rejects_before_model_call() -> None:
    gateway = ScriptedModelGateway([])
    result = await agent(gateway).run(
        invocation=invocation(deadline_at=datetime.now(UTC) - timedelta(seconds=1)),
        context=context(),
    )
    assert result.verdict.verdict == "reject"
    assert result.failure_code == "deadline_exceeded"
    assert gateway.requests == []


async def test_exhausted_tokens_cannot_accept_a_pass() -> None:
    gateway = ScriptedModelGateway([response('{"verdict":"pass"}')])
    result = await agent(gateway).run(invocation=invocation(max_total_tokens=20), context=context())
    assert result.verdict.verdict == "reject"
    assert result.failure_code == "token_budget_exhausted"
    assert result.total_token_count == 30
    assert len(gateway.requests) == 1
