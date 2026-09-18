import pytest
from pydantic import ValidationError

from slim_guard.expression_style.package import (
    Evidence,
    FixedExample,
    Guide,
    GuideRule,
    StylePackage,
    make_package,
)
from slim_guard.expression_style.trainer.construction import validate_proposal
from slim_guard.expression_style.trainer.contracts import (
    CandidateProposal,
    Material,
    MaterialAnalysis,
)
from slim_guard.expression_style.trainer.contracts import (
    TestSuite as FrozenTestSuite,
)
from slim_guard.expression_style.trainer.datasets import validate_suite

from .fakes import suite_payload


def guide():
    return Guide(
        summary="简洁",
        rules=(
            GuideRule(
                rule_id="r",
                text="少铺垫",
                boundary="不删除事实",
                evidence=(Evidence(example_id="e", revision=1, quote="不客气"),),
            ),
        ),
    )


def test_package_is_deterministic_and_does_not_have_a_fixed_example_count():
    examples = tuple(
        FixedExample(
            id=f"e{i}",
            revision=1,
            original_response="不用客气",
            desired_response="不客气",
            rule_ids=("r",),
        )
        for i in range(9)
    )
    first = make_package("doctor", "v", "医生", guide(), examples)
    assert first == make_package("doctor", "v", "医生", guide(), examples)
    assert len(first.runtime_snapshot().examples) == 9
    assert "evidence" not in first.compiled_prompt
    tampered = first.model_dump(mode="json")
    tampered["compiled_prompt"] += "篡改"
    with pytest.raises(ValidationError):
        StylePackage.model_validate(tampered)


@pytest.mark.parametrize("role", ["negative", "content_change", "semantic_drift", "unusable"])
def test_only_positive_material_can_be_a_fixed_example(role):
    m = Material(
        id="e",
        revision=1,
        user_input="谢谢",
        original_response="不用客气",
        desired_response="不客气",
    )
    with pytest.raises(ValueError):
        validate_proposal(
            CandidateProposal(guide=guide(), example_ids=("e",), explanation="选择"),
            (m,),
            (MaterialAnalysis(example_id="e", role=role, reason="原因"),),
            "doctor",
            "v",
            "医生",
        )


def test_evidence_must_reference_actual_revision_and_verbatim_text():
    m = Material(
        id="e",
        revision=2,
        user_input="谢谢",
        original_response="不用客气",
        desired_response="不客气",
    )
    with pytest.raises(ValueError, match="修订"):
        validate_proposal(
            CandidateProposal(guide=guide(), example_ids=("e",), explanation="选择"),
            (m,),
            (MaterialAnalysis(example_id="e", role="positive", reason="原因"),),
            "doctor",
            "v",
            "医生",
        )


def test_independent_suite_excludes_material_and_consumed_test_families():
    suite = FrozenTestSuite.model_validate(suite_payload())
    validate_suite(suite, (), [])
    with pytest.raises(ValueError, match="题族"):
        validate_suite(suite, (), [suite.acceptance[0].model_dump()])
    m = Material(
        id="m",
        revision=1,
        user_input=suite.development[0].user_input,
        original_response="原文",
        desired_response="期望",
    )
    with pytest.raises(ValueError, match="复述"):
        validate_suite(suite, (m,), [])


def test_dev_and_holdout_family_overlap_and_number_swaps_are_rejected():
    raw = suite_payload()
    raw["acceptance"][0]["family"] = raw["development"][0]["family"]
    with pytest.raises(ValueError, match="题族重叠"):
        validate_suite(FrozenTestSuite.model_validate(raw), (), [])
    raw = suite_payload()
    raw["acceptance"][0]["user_input"] = "我的体重是 75kg"
    raw["development"][0]["user_input"] = "我的体重是 78kg"
    with pytest.raises(ValueError, match="替换数字"):
        validate_suite(FrozenTestSuite.model_validate(raw), (), [])


def test_oversized_fixed_prompt_is_rejected_not_silently_truncated():
    examples = tuple(
        FixedExample(
            id=str(i),
            revision=1,
            original_response="原" * 4000,
            desired_response="期" * 4000,
            rule_ids=("r",),
        )
        for i in range(2)
    )
    with pytest.raises(ValueError, match="预算"):
        make_package("doctor", "v", "医生", guide(), examples)
