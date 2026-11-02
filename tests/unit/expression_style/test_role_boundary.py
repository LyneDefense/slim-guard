"""Speaker duties are not trainable taste, even when the source is already wrong."""

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agents.style.agent import ResponseStyleAgent
from slim_guard.expression_style.package import FixedExample, make_package
from slim_guard.expression_style.review.service import StyleReviewService
from slim_guard.expression_style.trainer.contracts import PreviewSuite
from slim_guard.expression_style.trainer.contracts import TestCase as PreviewCase
from slim_guard.expression_style.trainer.role_scope import scope_preview
from slim_guard.group_chat import COACH_ROLE_POLICY, COACH_ROLE_VIOLATION
from slim_guard.runtime.invocation import InvocationRunner

from .test_package_and_datasets import guide
from .test_shared_review import context, decision, invocation, response, styled
from .test_structured_output import client_for


@pytest.mark.parametrize(
    "source",
    [
        "记录没保存成功，核实一下重新发。",
        "这餐已经记录成功。",
        "我帮你修改刚才那条餐食记录。",
        "正在保存，请稍等。",
        "为了完成记录，请确认这是早餐还是晚餐。",
    ],
)
async def test_faithful_operation_report_is_still_rejected_as_coach(source):
    review = decision(passed=False, dimension="role", severity="blocking", excerpt=source)
    review["issues"][0].update(
        code=COACH_ROLE_VIOLATION,
        source_excerpt=source,
        explanation="操作说明属于系统助手，原稿已经越界。",
        repair_requirement="取消医生候选，操作说明保留给系统助手，不靠改写删除事实。",
    )
    gateway = ScriptedModelGateway([response(review)])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(source),
        response=styled(source),
        source_text=source,
    )
    assert not result.passed and result.verdict == "blocked"
    assert result.failure_code == COACH_ROLE_VIOLATION
    assert (
        result.checks[1]["status"] == "passed"
    )  # Fidelity alone cannot grant a speaker permission.
    assert result.checks[2]["status"] == "failed"
    assert COACH_ROLE_POLICY in gateway.requests[0].messages[0].content


@pytest.mark.parametrize(
    "source",
    [
        "从最近几天的饮食记录看，蔬菜种类比以前丰富。",
        "你确定喝了50升水？",
        "记录里有米饭、鸡肉和青菜，搭配比较完整。",
    ],
)
async def test_commentary_on_records_and_natural_questions_remain_allowed(source):
    gateway = ScriptedModelGateway([response(decision())])
    result = await StyleReviewService(InvocationRunner(model=gateway), "test").review(
        invocation=invocation(),
        context=context(source),
        response=styled(source),
        source_text=source,
    )
    assert result.passed and result.failure_code is None


async def test_wrong_role_source_is_not_repaired_by_deleting_its_original_meaning():
    source = "这餐已经记录成功。"
    check = decision(passed=False, dimension="role", severity="blocking", excerpt=source)
    check["issues"][0]["source_excerpt"] = source
    gateway = ScriptedModelGateway([response(styled(source).model_dump()), response(check)])
    result = await ResponseStyleAgent(runner=InvocationRunner(model=gateway), model="test").run(
        invocation=invocation(), context=context(source)
    )
    assert result.used_fallback and result.failure_code == COACH_ROLE_VIOLATION
    assert (
        result.response.text == source
    )  # Diagnostics preserve the input; delivery must suppress it.
    assert not result.repair_attempted and len(gateway.requests) == 2


def scope_sources():
    suite = PreviewSuite(
        version="test",
        cases=(
            PreviewCase(
                id="operation", family="legacy", user_input="记一下", source_text="没保存成功。"
            ),
            PreviewCase(
                id="water", family="chat", user_input="喝了50升水", source_text="你确定喝了50升水？"
            ),
            PreviewCase(
                id="records",
                family="commentary",
                user_input="搭配如何",
                source_text="从饮食记录看，蔬菜更多样了。",
            ),
        ),
    )
    examples = (
        FixedExample(
            id="good",
            revision=1,
            original_response="不用客气",
            desired_response="不客气",
            rule_ids=("r",),
        ),
        FixedExample(
            id="legacy",
            revision=1,
            original_response="已保存。",
            desired_response="记好了。",
            rule_ids=("r",),
        ),
    )
    package = make_package("doctor", "v", "医生", guide(), examples)
    verdicts = {
        "case:operation": False,
        "case:water": True,
        "case:records": True,
        "example:good": True,
        "example:legacy": False,
    }
    return (
        package,
        suite,
        {
            "items": [
                {"source_id": f"m{i + 1}", "allowed": allowed, "reason": "语义职责判定"}
                for i, allowed in enumerate(verdicts.values())
            ]
        },
    )


async def test_legacy_sources_and_inherited_examples_are_excluded_not_rewritten():
    package, suite, verdict = scope_sources()
    client, gateway, port = client_for([response(verdict)])
    selected, preview, decisions = await scope_preview(client, package, suite)
    assert [c.id for c in preview.cases] == ["water", "records"]
    assert [e.id for e in selected.examples] == ["good"]
    assert selected.package_hash != package.package_hash and len(package.examples) == 2
    assert decisions[0]["source_text"] == "没保存成功。" and not decisions[0]["allowed"]
    assert port.checkpoints["coach_scope"] == decisions
    assert COACH_ROLE_POLICY in gateway.requests[0].messages[0].content


async def test_scope_requires_complete_ids_and_repairs_missing_coverage():
    package, suite, verdict = scope_sources()
    invalid = {"items": [verdict["items"][0]] * len(verdict["items"])}
    client, gateway, _ = client_for([response(invalid), response(verdict)])
    _, preview, _ = await scope_preview(client, package, suite)
    assert len(preview.cases) == 2 and len(gateway.requests) == 2
    assert "validation_errors" in gateway.requests[1].messages[-1].content


async def test_scope_fails_closed_on_persistent_coverage_errors():
    package, suite, _ = scope_sources()
    client, _, port = client_for([response({"items": []})] * 3)
    with pytest.raises(ValueError, match="输出校验失败"):
        await scope_preview(client, package, suite)
    assert "coach_scope" not in port.checkpoints
