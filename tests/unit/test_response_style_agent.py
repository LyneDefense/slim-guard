from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.errors import ModelTimeoutError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse, ModelUsage
from slim_guard.agents.contracts import (
    AgentInvocation,
    ProfessionalAction,
    ProfessionalAssessment,
    ProfessionalClaim,
    ResponseContentBlock,
    ResponsePlan,
    StyledResponse,
)
from slim_guard.agents.structured_runner import StructuredAgentRunner
from slim_guard.agents.style import (
    SLIMGUARD_DEFAULT_V1,
    NeutralRenderer,
    ResponseStyleAgent,
    StyleContext,
    StyleContextCompiler,
    StyleExample,
    StyleIntegrityIssueCode,
    StyleResponseValidator,
)


def response_plan() -> ResponsePlan:
    return ResponsePlan(
        communication_act="explain",
        requested_detail="normal",
        content_blocks=(
            ResponseContentBlock(
                block_id="fact-block",
                kind="fact",
                text="今天体重 77.6kg，已经记录。",
                source_refs=("weight-record-1",),
            ),
            ResponseContentBlock(
                block_id="claim-block",
                kind="claim",
                text="最近 7 天趋势平稳。",
                source_refs=("claim-1",),
            ),
            ResponseContentBlock(
                block_id="action-block",
                kind="action",
                text="明天继续在起床后称重。",
                source_refs=("action-1",),
            ),
            ResponseContentBlock(
                block_id="risk-block",
                kind="risk",
                text="如果持续头晕，请及时就医。",
                source_refs=("risk-1",),
            ),
            ResponseContentBlock(
                block_id="uncertainty-block",
                kind="uncertainty",
                text="目前数据还不足以判断长期变化。",
                source_refs=("claim-1",),
            ),
        ),
        citation_refs=("citation-1",),
        prohibited_transformations=("do_not_change_record_status",),
    )


def assessment() -> ProfessionalAssessment:
    return ProfessionalAssessment(
        assessment_type="progress",
        overall="近期趋势平稳",
        findings=(
            ProfessionalClaim(
                claim_id="claim-1",
                category="weight_trend",
                statement="最近七天趋势平稳",
                basis_types=("user_evidence",),
                evidence_refs=("weight-record-1",),
                confidence="medium",
            ),
        ),
        actions=(
            ProfessionalAction(
                action_id="action-1",
                statement="明天继续称重",
                basis_claim_ids=("claim-1",),
            ),
        ),
        risk_flags=("risk-1",),
    )


def style_context() -> StyleContext:
    return StyleContextCompiler().compile(
        turn_id="turn-1",
        response_plan=response_plan(),
        assessment=assessment(),
    )


def valid_styled_response() -> StyledResponse:
    return StyledResponse(
        text=(
            "今天体重 77.6kg，已经记录。\n"
            "最近 7 天趋势平稳。\n"
            "明天继续在起床后称重。\n"
            "如果持续头晕，请及时就医。\n"
            "目前数据还不足以判断长期变化。"
        ),
        used_block_ids=(
            "fact-block",
            "claim-block",
            "action-block",
            "risk-block",
            "uncertainty-block",
        ),
        used_claim_ids=("claim-1",),
        used_action_ids=("action-1",),
        preserved_risk_flags=("risk-1",),
        preserved_citation_refs=("citation-1",),
        style_profile_version="slimguard_default_v1",
    )


def invocation(**updates: object) -> AgentInvocation:
    values: dict[str, object] = {
        "invocation_id": "style-invocation-1",
        "trace_id": "trace-1",
        "turn_id": "turn-1",
        "graph_version": "typed-supervisor-v1",
        "agent_role": "response_style",
        "agent_version": "response-style-v1",
        "deadline_at": datetime.now(UTC) + timedelta(seconds=30),
        "max_model_calls": 2,
        "max_tool_calls": 0,
        "max_total_tokens": 4096,
    }
    values.update(updates)
    return AgentInvocation.model_validate(values)


def model_response(response: StyledResponse, *, tokens: int = 50) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=response.model_dump_json(),
        ),
        usage=ModelUsage(total_tokens=tokens),
    )


def test_style_profile_and_context_are_version_scoped() -> None:
    assert SLIMGUARD_DEFAULT_V1.version == "slimguard_default_v1"
    assert SLIMGUARD_DEFAULT_V1.preferred_max_paragraphs == 3

    wrong_example = StyleExample(
        example_id="example-1",
        style_profile_version="another-profile-v1",
        communication_act="explain",
        text="示例",
    )
    with pytest.raises(ValidationError, match="selected profile version"):
        StyleContextCompiler().compile(
            turn_id="turn-1",
            response_plan=response_plan(),
            examples=(wrong_example,),
        )


def test_neutral_renderer_preserves_all_blocks_and_reference_sets() -> None:
    context = style_context()
    response = NeutralRenderer().render(context)

    assert response.text.splitlines() == [
        block.text for block in context.response_plan.content_blocks
    ]
    assert response.used_claim_ids == ("claim-1",)
    assert response.used_action_ids == ("action-1",)
    assert response.preserved_risk_flags == ("risk-1",)
    assert response.preserved_citation_refs == ("citation-1",)
    assert StyleResponseValidator().validate(context, response).is_valid


def test_validator_rejects_changed_content_numbers_and_reference_sets() -> None:
    context = style_context()
    changed = StyledResponse(
        text="今天体重 76kg，已经记录。最近 8 天趋势平稳。",
        used_block_ids=("fact-block", "unknown-block"),
        used_claim_ids=("invented-claim",),
        used_action_ids=(),
        preserved_risk_flags=(),
        preserved_citation_refs=("invented-citation",),
        style_profile_version="another-profile-v1",
    )

    report = StyleResponseValidator().validate(context, changed)
    codes = set(report.issue_codes)

    assert StyleIntegrityIssueCode.PROFILE_VERSION_CHANGED in codes
    assert StyleIntegrityIssueCode.REQUIRED_BLOCK_OMITTED in codes
    assert StyleIntegrityIssueCode.PROTECTED_BLOCK_OMITTED in codes
    assert StyleIntegrityIssueCode.UNKNOWN_BLOCK_ADDED in codes
    assert StyleIntegrityIssueCode.CLAIM_REFERENCE_CHANGED in codes
    assert StyleIntegrityIssueCode.ACTION_REFERENCE_CHANGED in codes
    assert StyleIntegrityIssueCode.RISK_REFERENCE_CHANGED in codes
    assert StyleIntegrityIssueCode.CITATION_REFERENCE_CHANGED in codes
    assert StyleIntegrityIssueCode.PROTECTED_CONTENT_CHANGED in codes
    assert StyleIntegrityIssueCode.NUMBER_CHANGED in codes


def test_validator_rejects_new_record_medical_or_advice_claims() -> None:
    plan = ResponsePlan(
        communication_act="acknowledge",
        content_blocks=(
            ResponseContentBlock(
                block_id="social",
                kind="social_act",
                text="收到。",
            ),
        ),
    )
    context = StyleContextCompiler().compile(turn_id="turn-1", response_plan=plan)
    response = StyledResponse(
        text="收到，已经保存。你得了糖尿病，建议每天绝食。",
        used_block_ids=("social",),
        style_profile_version="slimguard_default_v1",
    )

    report = StyleResponseValidator().validate(context, response)

    assert StyleIntegrityIssueCode.UNSUPPORTED_CONTENT_ADDED in set(report.issue_codes)


def test_validator_rejects_an_invented_need_or_next_step() -> None:
    plan = ResponsePlan(
        communication_act="explain",
        content_blocks=(
            ResponseContentBlock(
                block_id="social",
                kind="social_act",
                text="目前还不能直接得出结论。",
            ),
        ),
    )
    context = StyleContextCompiler().compile(turn_id="turn-1", response_plan=plan)
    response = StyledResponse(
        text="目前还不能直接得出结论。下一步需要补齐更多信息。",
        used_block_ids=("social",),
        style_profile_version="slimguard_default_v1",
    )

    report = StyleResponseValidator().validate(context, response)

    assert StyleIntegrityIssueCode.UNSUPPORTED_CONTENT_ADDED in set(report.issue_codes)


async def test_style_agent_returns_valid_model_response_without_repair() -> None:
    gateway = ScriptedModelGateway((model_response(valid_styled_response()),))
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(invocation=invocation(), context=style_context())

    assert result.status.value == "succeeded"
    assert result.response == valid_styled_response()
    assert result.model_call_count == 1
    assert result.used_fallback is False
    assert result.repair_attempted is False
    assert gateway.requests[0].purpose.value == "response_style"
    assert gateway.requests[0].tools == ()
    assert gateway.requests[0].output_schema_name == "StyledResponse"
    prompt = gateway.requests[0].messages[0].content
    assert "StyledResponse JSON schema" in prompt
    assert '"used_block_ids"' in prompt
    assert "do not return the input StyleContext" in prompt
    assert "not/is correction contrast" in prompt
    assert "Never copy placeholder scaffolding" in prompt
    assert "when the ResponsePlan supplies no action" in prompt
    payload = json.loads(gateway.requests[0].messages[-1].content)
    assert StyleContext.model_validate(payload["style_context"]) == style_context()
    assert payload["output_requirements"] == {
        "required_block_ids": [
            "fact-block",
            "claim-block",
            "action-block",
            "risk-block",
            "uncertainty-block",
        ],
        "used_claim_ids_exact": ["claim-1"],
        "used_action_ids_exact": ["action-1"],
        "preserved_risk_flags_exact": ["risk-1"],
        "preserved_citation_refs_exact": ["citation-1"],
        "style_profile_version_exact": "slimguard_default_v1",
        "no_action_may_be_added": False,
    }


async def test_style_agent_repairs_a_semantically_invalid_json_response_once() -> None:
    invalid = valid_styled_response().model_copy(
        update={"text": "今天体重 88kg，已经记录。"}
    )
    gateway = ScriptedModelGateway(
        (
            model_response(invalid, tokens=30),
            model_response(valid_styled_response(), tokens=40),
        )
    )
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(invocation=invocation(), context=style_context())

    assert result.status.value == "succeeded"
    assert result.repair_attempted is True
    assert result.model_call_count == 2
    assert result.total_token_count == 70
    assert "number_changed" in (gateway.requests[1].messages[-1].content or "")


async def test_style_agent_repairs_invalid_json_once() -> None:
    malformed = ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content="not-json")
    )
    gateway = ScriptedModelGateway((malformed, model_response(valid_styled_response())))
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(invocation=invocation(), context=style_context())

    assert result.status.value == "succeeded"
    assert result.repair_attempted is True
    assert result.model_call_count == 2
    assert json.loads(result.response.model_dump_json())["text"].startswith("今天体重")


async def test_style_agent_degrades_to_neutral_after_second_invalid_response() -> None:
    invalid = valid_styled_response().model_copy(update={"used_claim_ids": ()})
    gateway = ScriptedModelGateway((model_response(invalid), model_response(invalid)))
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(invocation=invocation(), context=style_context())

    assert result.status.value == "degraded"
    assert result.used_fallback is True
    assert result.repair_attempted is True
    assert result.failure_code == "style_integrity_invalid_after_repair"
    assert result.response == NeutralRenderer().render(style_context())


async def test_style_agent_degrades_on_model_failure_and_never_grants_tools() -> None:
    gateway = ScriptedModelGateway((ModelTimeoutError("planned timeout"),))
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(
        invocation=invocation(),
        context=style_context(),
    )

    assert result.status.value == "degraded"
    assert result.used_fallback is True
    assert result.failure_code == "model_gateway_error"
    assert result.response == NeutralRenderer().render(style_context())
    assert result.model_call_count == 0
    assert len(gateway.requests) == 1
    assert gateway.requests[0].tools == ()


async def test_style_agent_rejects_tool_permissions_before_calling_model() -> None:
    gateway = ScriptedModelGateway(())
    agent = ResponseStyleAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-style-model",
    )

    result = await agent.run(
        invocation=invocation(allowed_tools=("record_weight",), max_tool_calls=1),
        context=style_context(),
    )

    assert result.status.value == "degraded"
    assert result.failure_code == "style_tools_not_allowed"
    assert gateway.requests == []
