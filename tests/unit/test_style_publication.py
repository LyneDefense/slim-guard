"""Synthetic TEST publications only; no real assets or human approvals are manufactured."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from slim_guard.agents.contracts import CommunicationAct, ResponseContentBlock, ResponsePlan
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleExample,
    StyleProfile,
    StyleProfileSnapshot,
)
from slim_guard.config import Settings
from slim_guard.db.models import StyleProfileVersionRecord
from slim_guard.db.session import Database
from slim_guard.main import create_app
from slim_guard.style_corpus import (
    StyleAssetBundle,
    StyleEvalJudgment,
    StyleEvalReport,
    StyleEvalResult,
    StyleEvaluationScenario,
)
from slim_guard.style_profiles import (
    StyleProfileAlreadyExists,
    StyleProfileIntegrityError,
    StyleProfileNotFound,
    StyleProfileRepository,
)
from slim_guard.style_reviews import style_ab_case_key

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def synthetic_export(version: str = "TEST-style-v1") -> dict[str, Any]:
    profile = StyleProfile(
        profile_id="TEST-style",
        version=version,
        display_name="TEST style",
        description="TEST expression-only profile",
        tone_rules=("TEST: concise",),
    )
    examples = tuple(
        StyleExample(
            example_id=f"TEST-example-{index}",
            style_profile_version=version,
            communication_act="explain" if index < 7 else "ask",
            text="TEST expression placeholder [fact]",
        )
        for index in range(8)
    )
    bundle = StyleAssetBundle(
        source_corpus_sha256="a" * 64,
        profile=profile,
        examples=examples,
    )
    judgment = StyleEvalJudgment(
        style_match=True,
        semantic_fidelity=True,
        privacy_preserved=True,
        no_impersonation_or_abuse=True,
        no_added_professional_claims=True,
        reason="TEST synthetic passing result",
    )
    evaluation = StyleEvalReport(
        bundle_sha256=hashlib.sha256(bundle.model_dump_json().encode()).hexdigest(),
        cases_sha256="b" * 64,
        model="TEST-offline-model",
        actor="TEST-evaluator",
        created_at=NOW.isoformat(),
        passed=True,
        missing_acts=(),
        results=tuple(
            StyleEvalResult(
                case_id=f"TEST-{act}", communication_act=act, passed=True, judgment=judgment
            )
            for act in (CommunicationAct.EXPLAIN, CommunicationAct.ASK)
        ),
    )
    return {**bundle.model_dump(mode="json"), "evaluation": evaluation.model_dump(mode="json")}


async def synthetic_approval(_repository, **binding):
    """Fake approval adapter used only to isolate this repository's publication tests."""
    acts = (
        list(CommunicationAct)
        if binding["candidate_profile_version"] == "doctor_strict_v1"
        else [CommunicationAct.EXPLAIN, CommunicationAct.ASK]
    )
    receipt = {
        "schema_version": "1",
        **binding,
        "case_count": len(acts),
        "case_reviews": [
            {
                "case_id": style_ab_case_key(binding["evaluation_sha256"], f"TEST-{act.value}"),
                "source_sample_sha256": "c" * 64,
                "scenario_sha256": "d" * 64,
                "review_id": "TEST-review",
                "actor": "TEST-human",
                "style_match": 5,
                "fidelity": 5,
                "appropriateness": 5,
                "reviewed_at": NOW.isoformat(),
            }
            for act in acts
        ],
        "reviewer_actors": ["TEST-human"],
        "reviewed_at": NOW.isoformat(),
        "required_communication_acts": [act.value for act in acts],
        "automated_judge_models": ["TEST-offline-model"],
    }
    receipt["approval_sha256"] = hashlib.sha256(
        json.dumps(
            receipt,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return receipt


async def publish(repository: StyleProfileRepository, bundle=None, **changes):
    confirmations = {
        "actor": "TEST-publisher",
        "privacy_confirmed": True,
        "expression_only_confirmed": True,
        "evaluation_reviewed": True,
        **changes,
    }
    return await repository.import_reviewed_bundle(bundle or synthetic_export(), **confirmations)


@pytest.fixture
async def repository(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'TEST-style.sqlite'}")
    await database.create_schema()
    try:
        yield StyleProfileRepository(database)
    finally:
        await database.close()


async def test_import_is_evaluated_append_only_and_examples_are_version_and_act_scoped(
    repository,
    monkeypatch,
):
    monkeypatch.setattr(StyleProfileRepository, "_require_bundle_approval", synthetic_approval)
    asset = await publish(repository)
    assert asset.status == "evaluated"
    assert await repository.get_active("TEST-style") is None
    snapshot = await repository.require_published("TEST-style-v1")
    assert len(snapshot.examples) == 8
    examples = await repository.get_examples("TEST-style-v1", CommunicationAct.EXPLAIN)
    assert [example.example_id for example in examples] == [f"TEST-example-{i}" for i in range(5)]
    assert len(await repository.get_examples("TEST-style-v1", CommunicationAct.ASK)) == 1
    assert await repository.get_examples("TEST-style-v2", CommunicationAct.ASK) == ()
    assert asset.publication["approval"]["reviewer_actors"] == ["TEST-human"]
    with pytest.raises(StyleProfileAlreadyExists, match="cannot be overwritten"):
        await publish(repository)
    assert (await repository.require_published("TEST-style-v1")) == snapshot


@pytest.mark.parametrize(
    "field", ["privacy_confirmed", "expression_only_confirmed", "evaluation_reviewed"]
)
async def test_actor_without_each_explicit_confirmation_cannot_publish(repository, field):
    with pytest.raises(ValueError, match="explicit human"):
        await publish(repository, **{field: False})
    assert await repository.get_profile_asset("TEST-style-v1") is None


async def test_publication_requires_database_backed_human_approval(repository, monkeypatch):
    calls = []

    async def denied(_repository, **binding):
        calls.append(binding)
        raise ValueError("TEST: no authenticated human approval")

    monkeypatch.setattr(StyleProfileRepository, "_require_bundle_approval", denied)
    with pytest.raises(ValueError, match="no authenticated human approval"):
        await publish(repository)
    assert len(calls) == 1
    assert calls[0]["candidate_profile_version"] == "TEST-style-v1"
    assert calls[0]["bundle_sha256"] == synthetic_export()["evaluation"]["bundle_sha256"]
    assert await repository.get_profile_asset("TEST-style-v1") is None


async def test_approving_only_a_subset_of_evaluated_cases_does_not_publish(repository, monkeypatch):
    async def incomplete(_repository, **binding):
        receipt = await synthetic_approval(_repository, **binding)
        receipt["case_reviews"].pop()
        return receipt

    monkeypatch.setattr(StyleProfileRepository, "_require_bundle_approval", incomplete)
    with pytest.raises(ValueError, match="exact evaluated case set"):
        await publish(repository)
    assert await repository.get_profile_asset("TEST-style-v1") is None


async def test_real_database_review_adapter_requires_all_test_case_acceptances(repository):
    """Exercise actual DB contracts with explicitly synthetic test scores and replies."""
    from slim_guard.style_reviews import (
        StyleABBundleNotApproved,
        StyleABHumanScore,
        StyleABPairImport,
        StyleABReviewRepository,
        TrustedStyleABSource,
    )

    data = synthetic_export()
    evaluation = StyleEvalReport.model_validate(data["evaluation"])
    evaluation_digest = hashlib.sha256(evaluation.model_dump_json().encode()).hexdigest()
    profile = StyleProfile.model_validate(data["profile"])
    pairs = []
    for result in evaluation.results:
        plan = ResponsePlan(
            communication_act=result.communication_act,
            content_blocks=(
                ResponseContentBlock(
                    block_id="TEST-block",
                    kind="social_act",
                    text="TEST synthetic expression",
                ),
            ),
        )
        baseline = NeutralRenderer().render(
            StyleContext(
                turn_id="TEST-turn",
                response_plan=plan,
                profile=SLIMGUARD_DEFAULT_V1,
            )
        )
        candidate = NeutralRenderer().render(
            StyleContext(
                turn_id="TEST-turn",
                response_plan=plan,
                profile=profile,
            )
        )
        pairs.append(
            StyleABPairImport(
                case_id=style_ab_case_key(evaluation_digest, result.case_id),
                source_sample_sha256="c" * 64,
                scenario=StyleEvaluationScenario(
                    title="TEST synthetic scenario",
                    user_situation="TEST user supplied synthetic input.",
                    known_context=("TEST context only.",),
                    response_goal="TEST comparison goal.",
                ),
                response_plan=plan,
                baseline_response=baseline,
                candidate_response=candidate,
                baseline_profile_version=SLIMGUARD_DEFAULT_V1.version,
                candidate_profile_version=profile.version,
                baseline_generation_model="TEST-model",
                candidate_generation_model="TEST-model",
                candidate_bundle_sha256=evaluation.bundle_sha256,
                automated_evaluation_sha256=hashlib.sha256(
                    evaluation.model_dump_json().encode()
                ).hexdigest(),
                automated_judge_model="TEST-model",
                automated_judge_status="passed",
            )
        )
    reviews = StyleABReviewRepository(repository.database)
    acts = (CommunicationAct.EXPLAIN, CommunicationAct.ASK)
    source = TrustedStyleABSource(
        source_id="TEST-source",
        imported_by="TEST-importer",
        manifest_sha256=reviews.pairs_manifest_sha256(
            pairs,
            source_id="TEST-source",
            required_communication_acts=acts,
        ),
        required_communication_acts=acts,
        synthetic_confirmed=True,
        deidentified_confirmed=True,
        expression_assets_reviewed=True,
    )
    case_ids = await reviews.import_pairs(pairs, trusted_source=source)
    with pytest.raises(StyleABBundleNotApproved):
        await publish(repository, data)
    for index, case_id in enumerate(case_ids):
        await reviews.submit_review(
            case_id=case_id,
            actor="TEST-human",
            score=StyleABHumanScore(
                style_match=5,
                fidelity=5,
                appropriateness=5,
                decision="accept",
                comment="TEST synthetic acceptance; not real publication evidence",
            ),
        )
        if index == 0:
            with pytest.raises(StyleABBundleNotApproved):
                await publish(repository, data)
    asset = await publish(repository, data)
    assert asset.status == "evaluated"
    assert asset.publication["approval"]["case_count"] == 2
    assert (await repository.require_published(profile.version)).profile == profile


@pytest.mark.parametrize(
    "tamper", ["profile", "example", "version", "failed", "missing_act", "missing_judgment"]
)
async def test_mismatched_or_incomplete_evaluation_cannot_publish(repository, tamper):
    data = synthetic_export()
    if tamper == "profile":
        data["profile"]["tone_rules"] = ["TEST modified after evaluation"]
    elif tamper == "example":
        data["examples"][0]["text"] = "TEST modified example"
    elif tamper == "version":
        data["examples"][0]["style_profile_version"] = "TEST-other-version"
    elif tamper == "failed":
        data["evaluation"]["results"][0]["judgment"]["semantic_fidelity"] = False
    elif tamper == "missing_act":
        data["evaluation"]["results"].pop()
    else:
        data["evaluation"]["results"][0]["judgment"] = None
    with pytest.raises(ValueError):
        await publish(repository, data)
    assert await repository.get_profile_asset("TEST-style-v1") is None


@pytest.mark.parametrize("status", ["draft", "retired", "active", "evaluated"])
async def test_unreviewed_version_never_crosses_runtime_boundary(repository, status):
    await repository.create_profile("TEST-unreviewed")
    await repository.append_version(
        profile_id="TEST-unreviewed",
        version="TEST-unreviewed-v1",
        status=status,
        display_name="TEST draft",
        description="TEST not human reviewed",
        tone_rules=("TEST rule",),
    )
    assert await repository.get_runtime_snapshot("TEST-unreviewed-v1") is None
    with pytest.raises(StyleProfileNotFound, match="not published"):
        await repository.require_published("TEST-unreviewed-v1")
    if status in {"draft", "retired"}:
        with pytest.raises(ValueError, match="cannot be activated"):
            await repository.activate(profile_id="TEST-unreviewed", version="TEST-unreviewed-v1")


async def test_stored_published_content_tampering_is_detected(repository, monkeypatch):
    monkeypatch.setattr(StyleProfileRepository, "_require_bundle_approval", synthetic_approval)
    await publish(repository)
    async with repository.database.session() as session, session.begin():
        row = await session.scalar(
            select(StyleProfileVersionRecord).where(
                StyleProfileVersionRecord.version == "TEST-style-v1",
            )
        )
        spec = json.loads(row.style_spec_json)
        spec["examples"][0]["text"] = "TEST unauthorized replacement"
        row.style_spec_json = json.dumps(spec)
    with pytest.raises(StyleProfileIntegrityError, match="content changed"):
        await repository.require_published("TEST-style-v1")


def test_snapshot_rejects_foreign_examples_and_is_immutable():
    data = synthetic_export()
    profile = StyleProfile.model_validate(data["profile"])
    wrong = StyleExample(
        example_id="TEST-foreign",
        style_profile_version="TEST-other",
        communication_act="ask",
        text="TEST placeholder",
    )
    with pytest.raises(ValidationError, match="selected profile version"):
        StyleProfileSnapshot(profile=profile, examples=(wrong,))
    snapshot = StyleProfileSnapshot(profile=SLIMGUARD_DEFAULT_V1)
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.profile = profile


def test_style_canary_configuration_is_independent_and_can_be_disabled():
    settings = Settings(
        _env_file=None,
        multi_agent_mode="off",
        style_canary_profile="TEST-style-v1",
        style_canary_user_ids="TEST-user-a, TEST-user-b,TEST-user-a",
    )
    assert settings.style_canary_users == frozenset({"TEST-user-a", "TEST-user-b"})
    assert settings.default_style_profile == "slimguard_default_v1"
    assert Settings(_env_file=None).style_canary_profile == ""
    disabled = Settings(_env_file=None, style_canary_user_ids="TEST-user")
    assert disabled.style_canary_profile == ""
    assert disabled.style_canary_users == frozenset({"TEST-user"})


@pytest.mark.parametrize("complete", [False, True])
async def test_doctor_strict_requires_all_six_expression_acts(repository, monkeypatch, complete):
    monkeypatch.setattr(StyleProfileRepository, "_require_bundle_approval", synthetic_approval)
    data = synthetic_export("doctor_strict_v1")
    data["profile"]["profile_id"] = "doctor_strict"
    if complete:
        for example, act in zip(data["examples"], CommunicationAct, strict=False):
            example["communication_act"] = act.value
        judgment = data["evaluation"]["results"][0]["judgment"]
        data["evaluation"]["results"] = [
            {
                "case_id": f"TEST-{act.value}",
                "communication_act": act.value,
                "passed": True,
                "judgment": judgment,
                "failure_code": None,
            }
            for act in CommunicationAct
        ]
    bundle = StyleAssetBundle.model_validate(
        {key: value for key, value in data.items() if key != "evaluation"}
    )
    data["evaluation"]["bundle_sha256"] = hashlib.sha256(
        bundle.model_dump_json().encode()
    ).hexdigest()
    if complete:
        asset = await publish(repository, data)
        assert asset.status == "evaluated"
        assert await repository.get_active("doctor_strict") is None
    else:
        with pytest.raises(ValueError, match="all six communication acts"):
            await publish(repository, data)


@pytest.mark.parametrize("field", ["default_style_profile", "style_canary_profile"])
async def test_startup_fails_closed_for_unpublished_configured_profile(tmp_path, field):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'TEST-startup.sqlite'}",
        **{field: "TEST-unpublished"},
    )
    app = create_app(settings)
    with pytest.raises(StyleProfileNotFound, match="not published"):
        async with app.router.lifespan_context(app):
            pytest.fail("Unpublished profile must not enter the application lifespan")
