"""Quantity fidelity belongs to the shared reviewer, not digit matching."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.agent_models.errors import ModelTimeoutError
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
from slim_guard.expression_style.review.integrity import StyleResponseValidator
from slim_guard.expression_style.review.service import StyleReviewService
from slim_guard.runtime.invocation import InvocationRunner


def context(source):
    return StyleContext(
        turn_id="quantity-turn",
        profile=SLIMGUARD_DEFAULT_V1,
        response_plan=ResponsePlan(
            content_blocks=(
                ResponseContentBlock(block_id="neutral", kind="social_act", text=source),
            )
        ),
    )


def invocation():
    return AgentInvocation(
        invocation_id="quantity-style",
        trace_id="quantity-trace",
        turn_id="quantity-turn",
        graph_version="test",
        agent_role="response_style",
        agent_version="test",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )


def styled(value):
    return StyledResponse(
        text=value,
        used_block_ids=("neutral",),
        style_profile_version=SLIMGUARD_DEFAULT_V1.version,
    )


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


def rejected(source, candidate, code, repair):
    return {
        "fidelity_passed": False,
        "role_passed": True,
        "expression_passed": True,
        "issues": [
            {
                "dimension": "fidelity",
                "severity": "repairable",
                "code": code,
                "explanation": "改写改变了原稿的数量事实或限定条件",
                "source_excerpt": source,
                "output_excerpt": candidate,
                "repair_requirement": repair,
            }
        ],
    }


@pytest.mark.parametrize(
    "source,candidate,code,repair",
    [
        ("今天喝水1500毫升。", "今天喝水2500毫升。", "quantity_changed", "恢复原来的饮水量"),
        (
            "昨天体重79kg，今天78kg。",
            "昨天体重78kg，今天79kg。",
            "quantity_owner_changed",
            "恢复各日期对应的体重，不要交换归属",
        ),
        ("今天喝水1.5升。", "今天喝水1500升。", "unit_changed", "修正换算后的单位"),
        (
            "今天散步30分钟，游泳20分钟。",
            "今天散步30分钟。",
            "quantity_omitted",
            "保留游泳及其时长，不是只保留数字集合",
        ),
        ("每天喝水1500毫升。", "每次喝水1500毫升。", "scope_changed", "保留每天的统计口径"),
        ("昨天散步30分钟。", "今天散步30分钟。", "time_changed", "保留昨天的日期归属"),
        ("今天没有游泳。", "今天游泳了。", "negation_changed", "保留没有游泳的否定含义"),
        (
            "如果份量相同，这餐可能不到500千卡。",
            "这餐是500千卡。",
            "qualifier_changed",
            "保留份量前提、范围与不确定性",
        ),
        (
            "今天散步30分钟。",
            "今天散步30分钟，明天增加到60分钟。",
            "quantity_added",
            "删除原稿没有的明天运动时长建议",
        ),
    ],
)
async def test_semantic_changes_reach_reviewer_and_cannot_pass_on_matching_digits(
    source, candidate, code, repair
):
    ctx = context(source)
    rewrite = styled(candidate)
    assert StyleResponseValidator().validate(ctx, rewrite).is_valid
    gateway = ScriptedModelGateway([response(rejected(source, candidate, code, repair))])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(), context=ctx, response=rewrite, source_text=source
    )
    assert not result.passed and result.failure_code == "style_review_failed"
    assert result.checks[0]["status"] == "passed"
    assert result.checks[1]["status"] == "failed"
    assert result.details[0]["code"] == code
    assert result.details[0]["source_excerpt"] == source
    assert result.details[0]["output_excerpt"] == candidate
    assert repair in result.issues[0]
    payload = json.loads(gateway.requests[0].messages[-1].content)
    assert payload["original"] == source and payload["rewritten"] == candidate


@pytest.mark.parametrize(
    "source,candidate",
    [
        ("今天喝水1500毫升。", "今天喝水1500毫升，这1500毫升是今天的饮水量。"),
        ("今天喝水1.5升。", "今天喝水1500毫升。"),
        ("这周运动了3次。", "这周运动了三次。"),
    ],
)
async def test_agent_adopts_equivalent_quantity_rewrite_without_repair_or_fallback(
    source, candidate
):
    gateway = ScriptedModelGateway(
        [
            response(styled(candidate).model_dump()),
            response(
                {
                    "fidelity_passed": True,
                    "role_passed": True,
                    "expression_passed": True,
                    "issues": [],
                }
            ),
        ]
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context(source)
    )
    assert result.response.text == candidate
    assert not result.used_fallback and not result.repair_attempted
    assert result.model_call_count == 2
    assert len(result.checks) == 1 and result.checks[0]["passed"]


async def test_quantity_repair_receives_grounded_differences_and_is_reviewed_again():
    source, wrong = "昨天体重79kg，今天78kg。", "昨天体重78kg，今天79kg。"
    repair = "昨天对应79kg，今天对应78kg；不要交换日期归属"
    detail = rejected(source, wrong, "quantity_owner_changed", repair)
    passed = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}
    gateway = ScriptedModelGateway(
        [
            response(styled(wrong).model_dump()),
            response(detail),
            response(styled(source).model_dump()),
            response(passed),
        ]
    )
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context(source)
    )
    assert not result.used_fallback and result.repair_attempted
    assert result.response.text == source
    feedback = gateway.requests[2].messages[-1].content
    for value in (
        source,
        wrong,
        repair,
        "quantity_owner_changed",
        "source_excerpt",
        "output_excerpt",
    ):
        assert value in feedback
    assert result.checks[0]["details"] == detail["issues"]
    assert len(gateway.requests) == 4
    assert [r.output_schema_name for r in gateway.requests] == [
        "StyledResponse",
        "ReplyCheck",
        "StyledResponse",
        "ReplyCheck",
    ]


async def test_two_semantic_quantity_failures_fall_back_instead_of_adopting_candidate():
    source, wrong = "每天喝水1500毫升。", "每次喝水1500毫升。"
    failure = rejected(source, wrong, "scope_changed", "保留每天的统计口径")
    gateway = ScriptedModelGateway([response(styled(wrong).model_dump()), response(failure)] * 2)
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context(source)
    )
    assert result.used_fallback and result.failure_code == "style_review_failed"
    assert result.response.text == source
    assert result.repair_attempted and len(result.checks) == 2
    assert all(c["check_results"][1]["status"] == "failed" for c in result.checks)
    assert len(gateway.requests) == 4


@pytest.mark.parametrize("failure", ["timeout", "invalid_schema", "invented_evidence"])
async def test_unavailable_or_unverifiable_semantic_review_never_approves_numeric_rewrite(failure):
    source, candidate = "今天喝水1.5升。", "今天喝水1500毫升。"
    review = response({"properties": {}})
    if failure == "timeout":
        review = ModelTimeoutError("synthetic reviewer timeout")
    elif failure == "invented_evidence":
        review = response(rejected("捏造的原文片段", candidate, "unit_changed", "核对原文"))
    gateway = ScriptedModelGateway([response(styled(candidate).model_dump()), review] * 2)
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context(source)
    )
    assert result.used_fallback and result.response.text == source
    assert result.failure_code == "style_review_failed"
    assert len(result.checks) == 2
    assert all(c["verdict"] == "inconclusive" for c in result.checks)
    assert all(c["check_results"][1]["status"] == "error" for c in result.checks)
