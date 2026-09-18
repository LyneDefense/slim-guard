import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.agents.contracts import (
    AgentInvocation,
    ResponseContentBlock,
    ResponsePlan,
    StyledResponse,
)
from slim_guard.agents.style.agent import ResponseStyleAgent
from slim_guard.expression_style.contracts import SLIMGUARD_DEFAULT_V1, StyleContext
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.expression_style.review.service import StyleReviewService
from slim_guard.runtime.invocation import InvocationRunner


def context(source="叫我 SlimGuard。"):
    return StyleContext(
        turn_id="t",
        user_input="怎么称呼你",
        profile=SLIMGUARD_DEFAULT_V1,
        response_plan=ResponsePlan(
            content_blocks=(
                ResponseContentBlock(block_id="neutral", kind="social_act", text=source),
            )
        ),
    )


def invocation(calls=4):
    return AgentInvocation(
        invocation_id="i",
        trace_id="trace",
        turn_id="t",
        graph_version="test",
        agent_role="response_style",
        agent_version="v",
        max_model_calls=calls,
        max_tool_calls=0,
        max_total_tokens=32000,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )


def styled(text):
    return StyledResponse(
        text=text, used_block_ids=("neutral",), style_profile_version=SLIMGUARD_DEFAULT_V1.version
    )


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


def decision(*, passed=True, dimension="fidelity", severity="repairable", excerpt="不错"):
    return {
        "fidelity_passed": passed if dimension == "fidelity" else True,
        "expression_passed": passed if dimension == "expression" else True,
        "issues": []
        if passed
        else [
            {
                "dimension": dimension,
                "severity": severity,
                "code": "answer_replaced",
                "explanation": "回答被鼓励替换",
                "source_excerpt": "SlimGuard",
                "output_excerpt": excerpt,
                "repair_requirement": "保留产品名称并回答称呼问题",
            }
        ],
    }


async def test_shared_review_can_run_without_trainer_or_agent():
    gateway = ScriptedModelGateway([response(decision())])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(),
        response=styled("叫我 SlimGuard 就行。"),
        source_text="叫我 SlimGuard。",
        remaining_tokens=5000,
    )
    assert result.passed and result.verdict == "passed"
    assert {c["name"] for c in result.checks} == {"integrity", "fidelity", "expression"}
    assert all(c["status"] == "passed" for c in result.checks)
    assert result.signature
    assert not gateway.requests[0].tools


@pytest.mark.parametrize("dimension", ["fidelity", "expression"])
async def test_agent_repairs_with_previous_output_and_specific_reason(dimension):
    gateway = ScriptedModelGateway(
        [
            response(styled("不错，要保持。").model_dump()),
            response(decision(passed=False, dimension=dimension)),
            response(styled("叫我 SlimGuard 就行。").model_dump()),
            response(decision()),
        ]
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context()
    )
    assert not result.used_fallback and result.repair_attempted
    assert len(result.checks) == 2
    assert result.checks[0]["output"] == "不错，要保持。"
    assert "保留产品名称" in gateway.requests[2].messages[-1].content
    assert "不错，要保持" in gateway.requests[2].messages[-1].content
    assert result.checks[1]["verdict"] == "passed"


@pytest.mark.parametrize(
    "bad",
    [
        {"fidelity_passed": True, "issues": []},
        {"fidelity_passed": "true", "expression_passed": True, "issues": []},
        {"fidelity_passed": False, "expression_passed": True, "issues": []},
        {"fidelity_passed": True, "expression_passed": True, "issues": [], "properties": {}},
    ],
)
def test_missing_checks_schema_echo_and_inconsistent_verdicts_are_rejected(bad):
    with pytest.raises(ValidationError):
        ReplyCheck.model_validate(bad)


async def test_invented_review_evidence_cannot_pass():
    gateway = ScriptedModelGateway([response(decision(passed=False, excerpt="捏造的引用"))])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(),
        response=styled("不错，要保持。"),
        source_text="叫我 SlimGuard。",
        remaining_tokens=5000,
    )
    assert not result.passed and result.verdict == "inconclusive"
    assert result.checks[-1]["status"] == "error"


async def test_integrity_failure_marks_other_checks_not_run():
    gateway = ScriptedModelGateway([])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context("体重 78kg。"),
        response=styled("体重 70kg。"),
        source_text="体重 78kg。",
        remaining_tokens=5000,
    )
    assert result.verdict == "needs_repair" and not gateway.requests
    assert result.checks[1]["status"] == "not_run"


async def test_review_budget_unavailability_is_not_success():
    gateway = ScriptedModelGateway([])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(),
        response=styled("叫我 SlimGuard。"),
        source_text="叫我 SlimGuard。",
        remaining_tokens=0,
    )
    assert not result.passed and result.verdict == "inconclusive"
    assert result.checks[-1]["status"] == "not_run"


async def test_no_unchecked_reply_when_second_attempt_has_no_budget():
    gateway = ScriptedModelGateway(
        [response(styled("不错，要保持。").model_dump()), response(decision(passed=False))]
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(calls=2), context=context()
    )
    assert result.used_fallback and result.response.text == "叫我 SlimGuard。"
    assert len(gateway.requests) == 2


@pytest.mark.parametrize(
    "source,rewritten",
    [
        ("收到。", "收到，已经保存。你得了糖尿病，建议每天绝食。"),
        ("目前还不能直接得出结论。", "目前还不能直接得出结论。下一步需要补齐更多信息。"),
        ("这次保存失败。", "这次保存成功。"),
        ("今天尚未记录。", "你就是不自律。"),
    ],
)
async def test_unsupported_additions_are_rejected_by_shared_semantic_review(source, rewritten):
    value = decision(passed=False, excerpt=rewritten)
    value["issues"][0]["source_excerpt"] = source
    value["issues"][0]["explanation"] = "增加了原稿没有的判断或操作状态"
    value["issues"][0]["repair_requirement"] = "恢复原稿含义，不新增判断"
    gateway = ScriptedModelGateway([response(value)])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(source),
        response=styled(rewritten),
        source_text=source,
        remaining_tokens=5000,
    )
    assert not result.passed and result.verdict == "needs_repair"
    assert result.checks[0]["status"] == "passed"
    assert result.checks[1]["status"] == "failed"


@pytest.mark.parametrize(
    "source,rewritten",
    [
        ("体重 75.0kg，体脂 20.0%。", "体重75kg，体脂20%。"),
        ("累计 1,000 次。", "累计1000次。"),
        ("不用客气，很高兴帮到你。", "不客气。"),
    ],
)
async def test_equivalent_numbers_and_shortening_reach_semantic_review(source, rewritten):
    gateway = ScriptedModelGateway([response(decision())])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(source),
        response=styled(rewritten),
        source_text=source,
        remaining_tokens=5000,
    )
    assert result.passed and len(gateway.requests) == 1


async def test_literal_contract_is_checked_inside_agent_and_drives_repair():
    protected_context = context().model_copy(update={"protected_literals": ("SlimGuard",)})
    gateway = ScriptedModelGateway(
        [
            response(styled("叫我小助手。").model_dump()),
            response(styled("叫我 SlimGuard 就行。").model_dump()),
            response(decision()),
        ]
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(),
        context=protected_context,
    )
    assert result.repair_attempted and not result.used_fallback
    assert "protected_literal_changed" in result.checks[0]["issues"]
    assert len(gateway.requests) == 3


def test_invalid_input_literal_contract_cannot_be_treated_as_safe_original():
    raw = context().model_dump()
    raw["protected_literals"] = ["不在原稿中的内容"]
    with pytest.raises(ValidationError, match="必须存在于原稿"):
        StyleContext.model_validate(raw)
