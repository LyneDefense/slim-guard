"""Prepared import/proposal tests use synthetic pairs, judgments and review actors only."""

from __future__ import annotations

import json

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.agents.contracts import ResponseContentBlock, ResponsePlan, StyledResponse
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.style_corpus import CorpusReview, OfflineStyleCorpus, StyleEvalCase
from slim_guard.wechat_style_export import PreparedExport, PreparedStylePair


@pytest.fixture
def corpus(tmp_path):
    value = OfflineStyleCorpus(tmp_path / "synthetic-prepared.sqlite")
    yield value
    value.close()


def pair(index=1, **changes):
    return PreparedStylePair.model_validate(
        {
            "pair_id": f"test-pair-{index}",
            "source_sha256": "a" * 64,
            "segment_id": f"test-segment-{index}",
            "context": f"[参与者{index}] 测试上下文。",
            "reply": "测试回复：[既定事实]。",
            "message_ids": (f"test-reply-{index}",),
            "context_message_ids": (f"test-context-{index}",),
            "eligible_for_judgment": True,
            "reply_type": "text",
            "context_sender_alias": f"[参与者{index}]",
            **changes,
        }
    )


def prepared(*pairs):
    return PreparedExport(source_sha256="a" * 64, pairs=pairs or (pair(),))


def response(payload):
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=json.dumps(payload))
    )


def judgment(**changes):
    return response(
        {
            "related": True,
            "communication_act": "acknowledge",
            "example_text": "测试示例：[既定事实]。",
            "tone_rules": ["测试：简洁确认。"],
            "reason": "测试：明确关联。",
            **changes,
        }
    )


def profile():
    return SLIMGUARD_DEFAULT_V1.model_copy(
        update={
            "profile_id": "test-candidate",
            "version": "test-candidate-v1",
            "display_name": "测试候选风格",
        }
    )


def review(decision="approve"):
    return CorpusReview(
        actor="TEST-only-human",
        decision=decision,
        note="测试人工审核记录。",
        privacy_confirmed=True,
        expression_only_confirmed=True,
    )


def eval_case(generation_status="succeeded"):
    return StyleEvalCase(
        case_id="synthetic-eval",
        response_plan=ResponsePlan(
            communication_act="acknowledge",
            content_blocks=(
                ResponseContentBlock(block_id="test-social", kind="social_act", text="测试确认。"),
            ),
        ),
        styled_response=StyledResponse(
            text="测试确认。",
            used_block_ids=("test-social",),
            style_profile_version=profile().version,
        ),
        generation_status=generation_status,
    )


async def test_repeated_prepared_import_reuses_content_model_identity_and_preserves_segments(
    corpus,
):
    gateway = ScriptedModelGateway([judgment(), judgment(), judgment()])
    source = prepared(pair(1), pair(2))
    first = await corpus.import_prepared_pairs(
        source, gateway=gateway, model="test-model", max_pairs=1
    )
    resumed = await corpus.import_prepared_pairs(
        source, gateway=gateway, model="test-model", max_pairs=2
    )
    repeated = await corpus.import_prepared_pairs(
        source, gateway=gateway, model="test-model", max_pairs=2
    )
    assert len(gateway.requests) == 2
    assert resumed == repeated
    assert resumed[0] == first[0]
    assert len(corpus.candidates()) == 2
    for index, candidate in enumerate(resumed, 1):
        assert candidate.prepared_pair_id == f"test-pair-{index}"
        assert candidate.source_message_ids == (f"test-reply-{index}",)
        assert candidate.context_message_ids == (f"test-context-{index}",)
        assert candidate.judge_model == "test-model"
        supplied = json.loads(gateway.requests[index - 1].messages[-1].content)
        assert supplied["context"] == source.pairs[index - 1].context
    changed_model = await corpus.import_prepared_pairs(
        prepared(pair()), gateway=gateway, model="another-test-model"
    )
    assert changed_model[0].candidate_id != first[0].candidate_id
    assert len(gateway.requests) == 3


async def test_pending_and_excluded_pairs_never_reach_a_model(corpus):
    gateway = ScriptedModelGateway([])
    source = prepared(
        pair(1, eligible_for_judgment=False, pending_reasons=("implicit_adjacency",)),
        pair(2, eligible_for_judgment=False, exclusion_reasons=("non_text_style_source",)),
    )
    assert await corpus.import_prepared_pairs(source, gateway=gateway, model="test-model") == ()
    assert not gateway.requests
    assert corpus.candidates() == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"source_sha256": "b" * 64},
        {"pending_reasons": ("unresolved_quote",)},
        {"exclusion_reasons": ("non_text_style_source",)},
        {"context": "测试手机13800138000"},
    ],
)
async def test_inconsistent_or_recognizably_private_prepared_pair_is_rejected_before_call(
    corpus, changes
):
    gateway = ScriptedModelGateway([])
    with pytest.raises(ValueError):
        await corpus.import_prepared_pairs(prepared(pair(**changes)), gateway=gateway, model="test")
    assert not gateway.requests


async def test_judge_introduced_private_information_is_not_saved(corpus):
    gateway = ScriptedModelGateway([judgment(example_text="测试泄露：test@example.com")])
    with pytest.raises(ValueError, match="private information"):
        await corpus.import_prepared_pairs(prepared(), gateway=gateway, model="test")
    assert corpus.candidates() == ()


async def test_proposal_is_explicitly_bound_but_cannot_export_even_after_a_passing_eval(corpus):
    imported = await corpus.import_prepared_pairs(
        prepared(), gateway=ScriptedModelGateway([judgment()]), model="test"
    )
    candidate_id = imported[0].candidate_id
    bundle = corpus.propose_bundle(profile=profile(), candidate_ids=[candidate_id])
    assert bundle.profile == profile()
    assert bundle.examples[0].style_profile_version == bundle.profile.version
    passing = response(
        {
            "style_match": True,
            "semantic_fidelity": True,
            "privacy_preserved": True,
            "no_impersonation_or_abuse": True,
            "no_added_professional_claims": True,
            "reason": "TEST pass",
        }
    )
    report = await corpus.evaluate(
        bundle,
        [eval_case()],
        gateway=ScriptedModelGateway([passing]),
        model="test",
        actor="TEST-evaluator",
    )
    assert report.passed
    with pytest.raises(ValueError, match="No human-approved"):
        corpus.export_bundle(bundle)
    corpus.review(candidate_id, review())
    with pytest.raises(ValueError, match="approvals changed"):
        corpus.export_bundle(bundle)


async def test_proposal_rejects_unknown_duplicate_unrelated_and_human_rejected_candidates(corpus):
    imported = await corpus.import_prepared_pairs(
        prepared(pair(1), pair(2)),
        gateway=ScriptedModelGateway([judgment(), judgment(related=False)]),
        model="test",
    )
    for ids in ([], ["unknown"], [imported[0].candidate_id] * 2, [imported[1].candidate_id]):
        with pytest.raises(ValueError):
            corpus.propose_bundle(profile=profile(), candidate_ids=ids)
    corpus.review(imported[0].candidate_id, review("reject"))
    with pytest.raises(ValueError, match="rejected"):
        corpus.propose_bundle(profile=profile(), candidate_ids=[imported[0].candidate_id])


@pytest.mark.parametrize("field", ["profile_id", "version", "display_name"])
async def test_profile_override_must_match_requested_bundle_identity(corpus, field):
    imported = await corpus.import_prepared_pairs(
        prepared(), gateway=ScriptedModelGateway([judgment()]), model="test"
    )
    corpus.review(imported[0].candidate_id, review())
    args = {
        "profile_id": profile().profile_id,
        "version": profile().version,
        "display_name": profile().display_name,
    }
    valid = corpus.build_bundle(**args, profile_override=profile())
    assert valid.profile == profile()
    assert all(example.style_profile_version == profile().version for example in valid.examples)
    with pytest.raises(ValueError, match="identity"):
        corpus.build_bundle(**{**args, field: "other-test-value"}, profile_override=profile())


async def test_degraded_renderer_cannot_be_scored_as_a_passing_style_case(corpus):
    imported = await corpus.import_prepared_pairs(
        prepared(), gateway=ScriptedModelGateway([judgment()]), model="test"
    )
    bundle = corpus.propose_bundle(profile=profile(), candidate_ids=[imported[0].candidate_id])
    gateway = ScriptedModelGateway([])
    report = await corpus.evaluate(
        bundle, [eval_case("degraded")], gateway=gateway, model="test", actor="TEST-evaluator"
    )
    assert not report.passed
    assert report.results[0].failure_code == "evaluation_failed"
    assert not gateway.requests
