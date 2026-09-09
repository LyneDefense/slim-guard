"""Database guarantees for synthetic A/B cases; no real human ratings or corpus data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.db.models import StyleABHumanReviewRecord
from slim_guard.db.session import Database
from slim_guard.style_evaluation import synthetic_style_suite
from slim_guard.style_reviews import (
    StyleABBundleNotApproved,
    StyleABCaseConflict,
    StyleABHumanScore,
    StyleABPairImport,
    StyleABReviewRepository,
    TrustedStyleABSource,
)


@pytest.fixture
async def ledger(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'synthetic.sqlite'}")
    await database.migrate()
    repository = StyleABReviewRepository(database)
    profile = SLIMGUARD_DEFAULT_V1.model_copy(update={"version": "TEST-candidate-v1"})
    cases = (synthetic_style_suite()[0], synthetic_style_suite()[10])
    pairs = []
    for case in cases:
        baseline, candidate = (
            NeutralRenderer().render(
                StyleContext(
                    turn_id="TEST-turn",
                    response_plan=case.response_plan,
                    profile=style,
                )
            )
            for style in (SLIMGUARD_DEFAULT_V1, profile)
        )
        pairs.append(
            StyleABPairImport(
                case_id=f"TEST-{case.case_id}",
                source_sample_sha256="a" * 64,
                scenario=case.scenario,
                response_plan=case.response_plan,
                baseline_response=baseline,
                candidate_response=candidate,
                baseline_profile_version=baseline.style_profile_version,
                candidate_profile_version=candidate.style_profile_version,
                baseline_generation_model="TEST-model",
                candidate_generation_model="TEST-model",
                candidate_bundle_sha256="b" * 64,
                automated_evaluation_sha256="c" * 64,
                automated_judge_model="TEST-judge",
                automated_judge_status="passed",
            )
        )
    acts = tuple(case.response_plan.communication_act for case in cases)
    source = TrustedStyleABSource(
        source_id="TEST-source",
        imported_by="TEST-operator",
        manifest_sha256=repository.pairs_manifest_sha256(
            pairs,
            source_id="TEST-source",
            required_communication_acts=acts,
        ),
        required_communication_acts=acts,
        synthetic_confirmed=True,
        deidentified_confirmed=True,
        expression_assets_reviewed=True,
    )
    ids = await repository.import_pairs(pairs, trusted_source=source)
    try:
        yield database, repository, tuple(pairs), source, ids
    finally:
        await database.close()


def score(**updates):
    return StyleABHumanScore.model_validate(
        {
            "style_match": 4,
            "fidelity": 5,
            "appropriateness": 4,
            "decision": "accept",
            "comment": "TEST ONLY invented human rating",
            **updates,
        }
    )


async def test_import_is_idempotent_and_list_omits_synthetic_reply_bodies(ledger):
    _, repository, pairs, source, ids = ledger
    assert await repository.import_pairs(pairs, trusted_source=source) == ids
    assert await repository.candidate_profile_versions() == ("TEST-candidate-v1",)
    listing = await repository.list_cases(limit=50, offset=0)
    assert listing["total"] == 2
    assert all("response_plan" not in row for row in listing["items"])
    assert all("response" not in row["candidate"] for row in listing["items"])
    assert all(row["scenario_title"] for row in listing["items"])
    detail = await repository.get_case(ids[0])
    assert detail["scenario"] == pairs[0].scenario.model_dump(mode="json")
    assert len(detail["scenario_sha256"]) == 64
    assert detail["candidate"]["response"]["text"] == pairs[0].candidate_response.text
    assert detail["reviews"] == []


async def test_conflicting_reimport_and_wrong_manifest_are_rejected(ledger):
    _, repository, pairs, source, _ = ledger
    changed = (pairs[0].model_copy(update={"candidate_generation_model": "TEST-other"}), pairs[1])
    with pytest.raises(ValueError, match="manifest"):
        await repository.import_pairs(changed, trusted_source=source)
    changed_source = source.model_copy(
        update={
            "manifest_sha256": repository.pairs_manifest_sha256(
                changed,
                source_id=source.source_id,
                required_communication_acts=source.required_communication_acts,
            )
        }
    )
    with pytest.raises(StyleABCaseConflict):
        await repository.import_pairs(changed, trusted_source=changed_source)


async def test_correction_keeps_history_changes_latest_stats_and_revokes_approval(ledger):
    _, repository, _, _, ids = ledger
    gate = dict(
        bundle_sha256="b" * 64,
        evaluation_sha256="c" * 64,
        candidate_profile_version="TEST-candidate-v1",
    )
    with pytest.raises(StyleABBundleNotApproved):
        await repository.require_bundle_approval(**gate)
    first = None
    for case_id in ids:
        review = await repository.submit_review(case_id=case_id, actor="TEST-human", score=score())
        if first is None:
            first = review
    receipt = await repository.require_bundle_approval(**gate)
    assert receipt["case_count"] == 2
    assert all(len(item["scenario_sha256"]) == 64 for item in receipt["case_reviews"])
    await repository.submit_review(
        case_id=ids[0],
        actor="TEST-second-human",
        score=score(decision="reject", style_match=1, corrects_review_id=first["review_id"]),
    )
    with pytest.raises(StyleABCaseConflict):
        await repository.submit_review(case_id=ids[0], actor="TEST-human", score=score())
    detail = await repository.get_case(ids[0])
    assert len(detail["reviews"]) == 2
    assert detail["latest_human_review"]["decision"] == "reject"
    statistics = await repository.statistics()
    assert statistics["counts"]["reviewed_case_count"] == 2
    assert statistics["rates"]["acceptance_rate"] == 0.5
    assert statistics["scores"]["style_match"]["average"] == 2.5
    with pytest.raises(StyleABBundleNotApproved):
        await repository.require_bundle_approval(**gate)


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_database_blocks_mutating_or_deleting_human_review_history(ledger, operation):
    database, repository, _, _, ids = ledger
    review = await repository.submit_review(case_id=ids[0], actor="TEST-human", score=score())
    statement = (
        "UPDATE style_ab_human_reviews SET actor = 'tampered' WHERE id = :id"
        if operation == "UPDATE"
        else "DELETE FROM style_ab_human_reviews WHERE id = :id"
    )
    with pytest.raises(IntegrityError, match="append-only"):
        async with database.engine.begin() as connection:
            await connection.execute(text(statement), {"id": review["review_id"]})
    assert (await repository.get_case(ids[0]))["latest_human_review"]["actor"] == "TEST-human"


async def test_database_rejects_second_initial_review_even_if_repository_is_bypassed(ledger):
    database, repository, _, _, ids = ledger
    await repository.submit_review(case_id=ids[0], actor="TEST-human", score=score())
    with pytest.raises(IntegrityError):
        async with database.engine.begin() as connection:
            await connection.execute(
                insert(StyleABHumanReviewRecord).values(
                    id="TEST-second-root",
                    case_id=ids[0],
                    supersedes_review_id=None,
                    actor="TEST-concurrent-human",
                    style_match=4,
                    fidelity=5,
                    appropriateness=4,
                    decision="accept",
                    comment="TEST only",
                    created_at=datetime.now(UTC) + timedelta(seconds=1),
                )
            )
    assert len((await repository.get_case(ids[0]))["reviews"]) == 1


@pytest.mark.parametrize("clock_offset", [0, -3600])
async def test_correction_chain_not_clock_order_determines_current_human_decision(
    ledger, clock_offset
):
    _, repository, _, _, ids = ledger
    first_time = datetime(2026, 1, 1, tzinfo=UTC)
    first = await repository.submit_review(
        case_id=ids[0],
        actor="TEST-human",
        score=score(),
        created_at=first_time,
    )
    correction = await repository.submit_review(
        case_id=ids[0],
        actor="TEST-human",
        score=score(decision="reject", corrects_review_id=first["review_id"]),
        created_at=first_time + timedelta(seconds=clock_offset),
    )
    assert (await repository.get_case(ids[0]))["latest_human_review"]["review_id"] == correction[
        "review_id"
    ]
    assert (await repository.statistics())["counts"]["rejected_case_count"] == 1
    # A subsequent review must correct the actual leaf, even when its clock was earlier.
    latest = await repository.submit_review(
        case_id=ids[0],
        actor="TEST-human",
        score=score(corrects_review_id=correction["review_id"]),
        created_at=first_time,
    )
    assert (await repository.get_case(ids[0]))["latest_human_review"]["review_id"] == latest[
        "review_id"
    ]
