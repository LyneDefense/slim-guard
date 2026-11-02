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
from slim_guard.expression_style.trainer.preview import fixed_suite, replay_suite


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


def test_fixed_previews_are_stable_and_known_material_is_labelled_as_replay():
    suite = fixed_suite()
    assert suite == fixed_suite()
    m = Material(
        id="m",
        revision=1,
        user_input="谢谢你的说明",
        original_response="原文",
        desired_response="期望",
    )
    analyses = (MaterialAnalysis(example_id="m", role="positive", reason="表达对"),)
    result = replay_suite(suite, (m,), analyses, [])
    assert len(result.cases) == len(suite.cases) + 1
    assert result.cases[-1].origin == "material"


def test_reusing_a_family_is_allowed_and_exact_replays_are_deduplicated():
    case = fixed_suite().cases[0].model_dump(mode="json")
    feedback = [{"case": case, "review": {"decision": "reject"}}] * 2
    result = replay_suite(fixed_suite(), (), (), feedback)
    assert len(result.cases) == len(fixed_suite().cases)


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
