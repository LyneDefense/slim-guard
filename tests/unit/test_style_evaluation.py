"""Synthetic A/B plumbing checks with scripted gateways, never real model evaluation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelPurpose, ModelResponse
from slim_guard.agents.contracts import CommunicationAct, ContentBlockKind
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleExample
from slim_guard.style_corpus import OfflineStyleCorpus, StyleAssetBundle
from slim_guard.style_evaluation import generate_style_comparisons, synthetic_style_suite


@pytest.fixture
def corpus(tmp_path):
    value = OfflineStyleCorpus(tmp_path / "synthetic-ab.sqlite")
    yield value
    value.close()


def bundle(*acts):
    profile = SLIMGUARD_DEFAULT_V1.model_copy(
        update={
            "profile_id": "test-ab-style",
            "version": "test-ab-style-v1",
            "display_name": "测试表达候选",
        }
    )
    examples = tuple(
        StyleExample(
            example_id=f"test-example-{index}",
            style_profile_version=profile.version,
            communication_act=act,
            text="测试表达：[既定内容]。",
        )
        for index, act in enumerate(acts or tuple(CommunicationAct))
    )
    return StyleAssetBundle(source_corpus_sha256="a" * 64, profile=profile, examples=examples)


def response(content):
    return ModelResponse(message=ModelMessage(role=MessageRole.ASSISTANT, content=content))


def script(asset, cases, *, failed_label=None):
    steps = []
    for case in cases:
        for label, profile in (("baseline", SLIMGUARD_DEFAULT_V1), ("candidate", asset.profile)):
            if label == failed_label:
                steps.extend([response("invalid JSON"), response("invalid JSON again")])
            else:
                rendered = NeutralRenderer().render(
                    StyleContext(
                        turn_id="test-only-turn", response_plan=case.response_plan, profile=profile
                    )
                )
                steps.append(response(rendered.model_dump_json()))
    if failed_label != "candidate":
        steps.extend(
            response(
                json.dumps(
                    {
                        "style_match": True,
                        "semantic_fidelity": True,
                        "privacy_preserved": True,
                        "no_impersonation_or_abuse": True,
                        "no_added_professional_claims": True,
                        "reason": "TEST ONLY synthetic evaluation",
                    }
                )
            )
            for _ in cases
        )
    return ScriptedModelGateway(steps)


def test_synthetic_suite_has_each_of_six_acts_with_social_and_protected_versions():
    cases = synthetic_style_suite()
    assert len(cases) == 12
    assert len({case.case_id for case in cases}) == 12
    assert all(case.synthetic and case.case_id.startswith("synthetic-") for case in cases)
    assert Counter(case.response_plan.communication_act for case in cases) == {
        act: 2 for act in CommunicationAct
    }
    assert all(case.scenario.title and case.scenario.user_situation for case in cases)
    assert all(case.scenario.known_context and case.scenario.response_goal for case in cases)
    assert len({case.scenario.title for case in cases}) == 12
    protected = [
        case
        for case in cases
        if any(block.kind is ContentBlockKind.FACT for block in case.response_plan.content_blocks)
    ]
    assert len(protected) == 6
    assert all(
        block.source_refs and block.source_refs[0].startswith("synthetic:")
        for case in protected
        for block in case.response_plan.content_blocks
        if block.kind is ContentBlockKind.FACT
    )


async def test_ab_generation_shares_plans_binds_examples_and_does_not_approve_or_publish(corpus):
    asset = bundle()
    cases = synthetic_style_suite()
    gateway = script(asset, cases)
    result = await generate_style_comparisons(
        bundle=asset,
        inputs=cases,
        gateway=gateway,
        model="test-scripted",
        corpus=corpus,
        actor="TEST-evaluator",
        redacted_inputs_confirmed=True,
    )
    assert len(result["cases"]) == 12
    assert result["comparison_complete"]
    assert result["comparable_case_count"] == 12
    assert result["human_review_status"] == "pending"
    assert result["published"] is False
    assert result["evaluation"]["passed"]
    assert result["bundle_sha256"] == hashlib.sha256(asset.model_dump_json().encode()).hexdigest()
    assert result["evaluation"]["bundle_sha256"] == result["bundle_sha256"]
    generation = [
        request for request in gateway.requests if request.purpose is ModelPurpose.RESPONSE_STYLE
    ]
    assert len(generation) == 24
    for index, case in enumerate(cases):
        baseline_payload = json.loads(generation[2 * index].messages[-1].content)
        candidate_payload = json.loads(generation[2 * index + 1].messages[-1].content)
        baseline = baseline_payload["style_context"]
        candidate = candidate_payload["style_context"]
        assert baseline["response_plan"] == candidate["response_plan"]
        assert result["cases"][index]["scenario"] == case.scenario.model_dump(mode="json")
        assert not baseline["examples"]
        assert len(candidate["examples"]) == 1
        assert candidate["examples"][0]["communication_act"] == case.response_plan.communication_act
        assert candidate["examples"][0]["style_profile_version"] == asset.profile.version
        assert result["cases"][index]["baseline"]["used_example_ids"] == []
        assert result["cases"][index]["candidate"]["used_example_ids"] == [
            candidate["examples"][0]["example_id"]
        ]
    gateway.assert_exhausted()


async def test_web_worker_evaluation_uses_the_same_judge_without_offline_sqlite():
    asset = bundle(CommunicationAct.ACKNOWLEDGE)
    cases = synthetic_style_suite()[:1]
    gateway = script(asset, cases)
    result = await generate_style_comparisons(
        bundle=asset,
        inputs=cases,
        gateway=gateway,
        model="test-scripted",
        corpus=None,
        actor="TEST-web-worker",
        redacted_inputs_confirmed=True,
    )
    assert result["evaluation"]["passed"] is True
    assert result["human_review_status"] == "pending"
    gateway.assert_exhausted()


async def test_web_worker_evaluation_does_not_require_offline_sqlite():
    asset = bundle(CommunicationAct.ACKNOWLEDGE)
    cases = synthetic_style_suite()[:1]
    gateway = script(asset, cases)
    result = await generate_style_comparisons(
        bundle=asset,
        inputs=cases,
        gateway=gateway,
        model="test-scripted",
        corpus=None,
        actor="TEST-web-worker",
        redacted_inputs_confirmed=True,
    )
    assert result["comparison_complete"]
    assert result["evaluation"]["passed"]
    gateway.assert_exhausted()


@pytest.mark.parametrize("failed_label", ["baseline", "candidate"])
async def test_renderer_fallback_is_never_counted_as_a_complete_ab_pair(corpus, failed_label):
    asset = bundle(CommunicationAct.ACKNOWLEDGE)
    cases = synthetic_style_suite()[:1]
    gateway = script(asset, cases, failed_label=failed_label)
    result = await generate_style_comparisons(
        bundle=asset,
        inputs=cases,
        gateway=gateway,
        model="test-scripted",
        corpus=corpus,
        actor="TEST-evaluator",
        redacted_inputs_confirmed=True,
    )
    assert not result["comparison_complete"]
    assert result["comparable_case_count"] == 0
    assert result["cases"][0][failed_label]["generation_status"] == "degraded"
    assert result["cases"][0][failed_label]["model_call_count"] == 2
    if failed_label == "candidate":
        assert not result["evaluation"]["passed"]
        assert not any(request.purpose is ModelPurpose.EVALUATION for request in gateway.requests)
    else:
        # Candidate quality and A/B comparability are deliberately separate facts.
        assert result["evaluation"]["passed"]
    assert result["published"] is False
    gateway.assert_exhausted()


async def test_only_five_matching_examples_are_sent_to_candidate_generation(corpus):
    asset = bundle(*([CommunicationAct.ACKNOWLEDGE] * 7))
    cases = synthetic_style_suite()[:1]
    gateway = script(asset, cases)
    result = await generate_style_comparisons(
        bundle=asset,
        inputs=cases,
        gateway=gateway,
        model="test-scripted",
        corpus=corpus,
        actor="TEST-evaluator",
        redacted_inputs_confirmed=True,
    )
    assert len(result["cases"][0]["candidate"]["used_example_ids"]) == 5
    assert result["cases"][0]["baseline"]["used_example_ids"] == []


@pytest.mark.parametrize("mismatch", ["foreign_profile", "duplicate_id"])
def test_bundle_rejects_invalid_example_binding_before_generation(mismatch):
    asset = bundle(CommunicationAct.ACKNOWLEDGE)
    payload = asset.model_dump()
    if mismatch == "foreign_profile":
        payload["examples"][0]["style_profile_version"] = "unrelated-profile"
    else:
        payload["examples"] = (*payload["examples"], payload["examples"][0])
    with pytest.raises(ValidationError):
        StyleAssetBundle.model_validate(payload)


@pytest.mark.parametrize("change", ["unconfirmed", "empty", "duplicate", "actor", "model"])
async def test_invalid_eval_authority_or_suite_is_rejected_before_any_model_call(corpus, change):
    cases = synthetic_style_suite()[:1]
    if change == "empty":
        cases = ()
    if change == "duplicate":
        cases = (*cases, *cases)
    gateway = ScriptedModelGateway([])
    with pytest.raises(ValueError):
        await generate_style_comparisons(
            bundle=bundle(),
            inputs=cases,
            gateway=gateway,
            model="" if change == "model" else "test-scripted",
            corpus=corpus,
            actor=" " if change == "actor" else "TEST-evaluator",
            redacted_inputs_confirmed=change != "unconfirmed",
        )
    assert not gateway.requests
