"""Offline A/B import checks using invented plans and a scripted model only."""

from __future__ import annotations

import hashlib
import json

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.agents.contracts import CommunicationAct
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleExample
from slim_guard.style_corpus import OfflineStyleCorpus, StyleAssetBundle, StyleEvalReport
from slim_guard.style_evaluation import generate_style_comparisons, synthetic_style_suite
from slim_guard.style_reviews import StyleABReviewRepository
from slim_guard.tools.manage_style_assets import prepare_ab_import


@pytest.fixture
async def generated(tmp_path):
    profile = SLIMGUARD_DEFAULT_V1.model_copy(
        update={"profile_id": "test-import", "version": "test-import-v1"}
    )
    bundle = StyleAssetBundle(
        source_corpus_sha256="a" * 64,
        profile=profile,
        examples=tuple(
            StyleExample(
                example_id=f"test-{act.value}",
                style_profile_version=profile.version,
                communication_act=act,
                text="测试表达：[既定内容]。",
            )
            for act in CommunicationAct
        ),
    )
    cases = synthetic_style_suite()
    contents = [
        NeutralRenderer()
        .render(StyleContext(turn_id="test-only", response_plan=case.response_plan, profile=style))
        .model_dump_json()
        for case in cases
        for style in (SLIMGUARD_DEFAULT_V1, profile)
    ]
    contents.extend(
        json.dumps(
            {
                "style_match": True,
                "semantic_fidelity": True,
                "privacy_preserved": True,
                "no_impersonation_or_abuse": True,
                "no_added_professional_claims": True,
                "reason": "TEST ONLY scripted judgment, not a real evaluation",
            }
        )
        for _ in cases
    )
    gateway = ScriptedModelGateway(
        [
            ModelResponse(message=ModelMessage(role=MessageRole.ASSISTANT, content=content))
            for content in contents
        ]
    )
    corpus = OfflineStyleCorpus(tmp_path / "test-only.sqlite")
    try:
        report = await generate_style_comparisons(
            bundle=bundle,
            inputs=cases,
            gateway=gateway,
            model="test-scripted",
            corpus=corpus,
            actor="TEST-evaluator",
            redacted_inputs_confirmed=True,
        )
        gateway.assert_exhausted()
        return bundle, report
    finally:
        corpus.close()


def prepare(bundle, data, **overrides):
    options = {
        "actor": "TEST-operator",
        "source_id": "test-synthetic-source",
        "reviewed_inputs_confirmed": True,
        **overrides,
    }
    return prepare_ab_import(data, bundle=bundle, **options)


def refresh_generated_checksum(report):
    report["generated_cases_sha256"] = hashlib.sha256(
        json.dumps(
            report["cases"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


async def test_generated_synthetic_pairs_import_with_exact_bindings_and_no_human_scores(generated):
    bundle, report = generated
    pairs, source = prepare(bundle, report)
    assert len(pairs) == 12
    assert prepare(bundle, report) == (pairs, source)
    assert set(source.required_communication_acts) == set(CommunicationAct)
    assert source.imported_by == "TEST-operator"
    assert source.synthetic_confirmed and source.deidentified_confirmed
    assert source.expression_assets_reviewed and not source.contains_raw_chat
    assert source.manifest_sha256 == StyleABReviewRepository.pairs_manifest_sha256(
        pairs,
        source_id=source.source_id,
        required_communication_acts=source.required_communication_acts,
    )
    evaluation_hash = hashlib.sha256(
        StyleEvalReport.model_validate(report["evaluation"]).model_dump_json().encode()
    ).hexdigest()
    for pair, raw in zip(pairs, report["cases"], strict=True):
        assert pair.response_plan.model_dump(mode="json") == raw["response_plan"]
        assert pair.baseline_response.model_dump(mode="json") == raw["baseline"]["response"]
        assert pair.candidate_response.model_dump(mode="json") == raw["candidate"]["response"]
        assert pair.candidate_profile_version == bundle.profile.version
        assert pair.baseline_profile_version == SLIMGUARD_DEFAULT_V1.version
        assert pair.candidate_bundle_sha256 == report["bundle_sha256"]
        assert pair.automated_evaluation_sha256 == evaluation_hash
        assert pair.automated_judge_status == "passed"
        assert pair.baseline_example_ids == ()
        assert pair.candidate_example_ids == (f"test-{pair.response_plan.communication_act.value}",)
        assert "human_score" not in pair.model_dump()


@pytest.mark.parametrize(
    "target", ["report_bundle", "evaluation_bundle", "evaluation_cases", "profile"]
)
async def test_tampered_report_hashes_and_profile_are_rejected(generated, target):
    bundle, report = generated
    if target == "report_bundle":
        report["bundle_sha256"] = "b" * 64
    elif target == "evaluation_bundle":
        report["evaluation"]["bundle_sha256"] = "b" * 64
    elif target == "evaluation_cases":
        report["evaluation"]["cases_sha256"] = "b" * 64
    else:
        report["candidate_profile_version"] = "unrelated-v1"
    with pytest.raises(ValueError):
        prepare(bundle, report)


async def test_changed_bundle_expression_is_not_the_evaluated_bundle(generated):
    bundle, report = generated
    changed = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"display_name": "Changed test style"})}
    )
    with pytest.raises(ValueError, match="exact bundle"):
        prepare(changed, report)


@pytest.mark.parametrize("label", ["baseline", "candidate"])
async def test_degraded_side_cannot_be_imported_despite_overall_passing_flag(generated, label):
    bundle, report = generated
    assert report["evaluation"]["passed"]
    report["cases"][0][label]["generation_status"] = "degraded"
    refresh_generated_checksum(report)
    with pytest.raises(ValueError, match="Degraded"):
        prepare(bundle, report)


@pytest.mark.parametrize("label", ["baseline", "candidate"])
async def test_generated_checksum_binds_both_sides_of_comparison(generated, label):
    bundle, report = generated
    report["cases"][0][label]["response"]["text"] = "TEST ONLY changed expression"
    with pytest.raises(ValueError, match="checksum"):
        prepare(bundle, report)


async def test_missing_generated_checksum_is_not_treated_as_legacy_trusted_report(generated):
    bundle, report = generated
    del report["generated_cases_sha256"]
    with pytest.raises(ValueError, match="checksum"):
        prepare(bundle, report)


async def test_rehashed_candidate_change_still_requires_evaluation_of_exact_response(generated):
    bundle, report = generated
    report["cases"][0]["candidate"]["response"]["text"] = "TEST ONLY different expression"
    refresh_generated_checksum(report)
    with pytest.raises(ValueError, match="actually evaluated"):
        prepare(bundle, report)


@pytest.mark.parametrize("synthetic", [False, None, "true", 1])
async def test_only_explicit_boolean_synthetic_cases_enter_ordinary_admin(generated, synthetic):
    bundle, report = generated
    report["cases"][0]["synthetic"] = synthetic
    refresh_generated_checksum(report)
    with pytest.raises(ValueError, match="synthetic"):
        prepare(bundle, report)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "foreign_case", "wrong_act"])
async def test_evaluation_results_must_cover_generated_cases_exactly(generated, kind):
    bundle, report = generated
    results = report["evaluation"]["results"]
    if kind == "missing":
        results.pop()
    elif kind == "duplicate":
        results[1] = results[0]
    elif kind == "foreign_case":
        results[0]["case_id"] = "test-unrelated-case"
    else:
        results[0]["communication_act"] = CommunicationAct.ASK.value
    with pytest.raises(ValueError):
        prepare(bundle, report)


@pytest.mark.parametrize("example_ids", [[], ["unrecognized"], ["test-ask"]])
async def test_candidate_examples_must_be_exact_act_specific_selection(generated, example_ids):
    bundle, report = generated
    report["cases"][0]["candidate"]["used_example_ids"] = example_ids
    refresh_generated_checksum(report)
    with pytest.raises(ValueError, match="example selection"):
        prepare(bundle, report)


@pytest.mark.parametrize("failure", ["result", "judgment", "failure_code", "missing_judgment"])
async def test_failed_automated_judgment_is_preserved_as_failed_for_human_review(
    generated, failure
):
    bundle, report = generated
    result = report["evaluation"]["results"][0]
    if failure == "result":
        result["passed"] = False
    elif failure == "judgment":
        result["judgment"]["semantic_fidelity"] = False
    elif failure == "failure_code":
        result["failure_code"] = "test-error"
    else:
        result["judgment"] = None
    # Overall flags do not override the actual per-case failure.
    pairs, _ = prepare(bundle, report)
    assert pairs[0].automated_judge_status == "failed"
    assert all(pair.automated_judge_status == "passed" for pair in pairs[1:])


@pytest.mark.parametrize(
    "overrides",
    [{"actor": " "}, {"source_id": " "}, {"reviewed_inputs_confirmed": False}],
)
async def test_import_requires_explicit_operator_and_actual_review_confirmation(
    generated, overrides
):
    bundle, report = generated
    with pytest.raises(ValueError):
        prepare(bundle, report, **overrides)
