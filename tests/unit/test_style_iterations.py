"""Durable style iteration control-plane tests use invented feedback only."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from slim_guard.agents.style.contracts import StyleProfileSnapshot
from slim_guard.db.models import StyleActivationEventRecord, StyleIterationInputRecord
from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)
from slim_guard.style_iteration_lifecycle import (
    StyleActivationAction,
    StyleRollbackAction,
    StyleRuntimeService,
    StyleRuntimeVersionResolver,
)
from slim_guard.style_iterations import (
    StyleIterationConflict,
    StyleIterationCreate,
    StyleIterationRepository,
)
from slim_guard.style_profiles import StyleProfileRepository


def correction(version: str = "TEST-doctor_v1") -> StyleCorrectionFeedbackInput:
    return StyleCorrectionFeedbackInput(
        profile_version=version,
        communication_act="explain",
        scenario="TEST 合成的新解释场景。",
        user_message="TEST 用户询问记录是否足够。",
        agent_response="TEST 当前回复太正式。",
        desired_response="TEST 记录太少，现在判断不了。",
        deidentified_confirmed=True,
        expression_only_confirmed=True,
    )


async def test_iteration_allocates_once_freezes_inputs_and_keeps_events(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'iterations.sqlite3'}")
    await database.migrate()
    feedback = await StyleCorrectionFeedbackRepository(database).append(
        correction(), actor="TEST-reviewer"
    )
    repository = StyleIterationRepository(database)
    payload = StyleIterationCreate(
        source_profile_version="TEST-doctor_v1",
        idempotency_key="TEST-idempotency-1",
        reviewed_inputs_confirmed=True,
    )
    first = await repository.create_run(payload, actor="TEST-admin", model_configured=True)
    repeated = await repository.create_run(payload, actor="TEST-admin", model_configured=True)
    assert first["run_id"] == repeated["run_id"]
    assert first["target_version"] == "TEST-doctor_v2"
    assert first["status"] == "queued"

    with pytest.raises(StyleIterationConflict, match="已有未结束"):
        await repository.create_run(
            payload.model_copy(update={"idempotency_key": "TEST-idempotency-2"}),
            actor="TEST-admin",
            model_configured=True,
        )

    claimed = await repository.claim_next(worker_id="TEST-worker", lease=timedelta(seconds=30))
    assert claimed is not None and claimed["status"] == "validating"
    frozen = await repository.freeze_inputs(first["run_id"])
    assert frozen["hashes"]["source_sha256"]
    assert frozen["artifacts"]["input_snapshot"]["counts"]["style_corrections"] == 1
    classifications = (
        {
            "source_kind": "style_feedback",
            "source_id": feedback["feedback_id"],
            "classification": "style_existing_act",
            "summary": "TEST 可归入解释行为。",
            "derived_case_ids": ["TEST-new-case"],
        },
    )
    await repository.store_classified_inputs(first["run_id"], classifications=classifications)
    await repository.store_classified_inputs(first["run_id"], classifications=classifications)
    async with database.session() as session:
        row = await session.scalar(text("SELECT COUNT(*) FROM style_iteration_inputs"))
        stored = await session.get(
            StyleIterationInputRecord,
            await session.scalar(text("SELECT id FROM style_iteration_inputs")),
        )
    assert row == 1
    assert stored is not None
    assert json.loads(stored.derived_case_ids_json) == ["TEST-new-case"]
    with pytest.raises(IntegrityError, match="append-only"):
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE style_iteration_inputs SET classification = 'tampered' WHERE id = :id"
                ),
                {"id": stored.id},
            )
    events = await repository.events(first["run_id"])
    assert [event["event_type"] for event in events] == [
        "run_created",
        "run_started",
        "stage_completed",
    ]
    await database.close()


async def test_iteration_requires_material_model_and_complete_source_reviews(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'requirements.sqlite3'}")
    await database.migrate()
    repository = StyleIterationRepository(database)
    payload = StyleIterationCreate(
        source_profile_version="TEST-doctor_v1",
        idempotency_key="TEST-idempotency-3",
        reviewed_inputs_confirmed=True,
    )
    with pytest.raises(StyleIterationConflict, match="model"):
        await repository.create_run(payload, actor="TEST-admin", model_configured=False)
    with pytest.raises(StyleIterationConflict, match="没有拒绝评分或新增风格纠正"):
        await repository.create_run(payload, actor="TEST-admin", model_configured=True)
    await database.close()


async def test_build_eligibility_reports_bounded_case_and_model_call_estimates(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'estimate.sqlite3'}")
    await database.migrate()
    await StyleCorrectionFeedbackRepository(database).append(correction(), actor="TEST-reviewer")
    eligibility = await StyleIterationRepository(database).build_eligibility("TEST-doctor_v1")
    assert eligibility["eligible"] is True
    assert eligibility["estimate"] == {
        "case_count_min": 12,
        "case_count_max": 13,
        "model_call_min": 38,
        "model_call_max": 41,
    }
    await database.close()


async def test_cancel_releases_profile_for_a_new_version_number(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'cancel.sqlite3'}")
    await database.migrate()
    await StyleCorrectionFeedbackRepository(database).append(correction(), actor="TEST-reviewer")
    repository = StyleIterationRepository(database)
    first = await repository.create_run(
        StyleIterationCreate(
            source_profile_version="TEST-doctor_v1",
            idempotency_key="TEST-idempotency-4",
            reviewed_inputs_confirmed=True,
        ),
        actor="TEST-admin",
        model_configured=True,
    )
    cancelled = await repository.cancel(first["run_id"], actor="TEST-admin", reason="TEST 主动停止")
    assert cancelled["status"] == "cancelled"
    second = await repository.create_run(
        StyleIterationCreate(
            source_profile_version="TEST-doctor_v1",
            idempotency_key="TEST-idempotency-5",
            reviewed_inputs_confirmed=True,
        ),
        actor="TEST-admin",
        model_configured=True,
    )
    assert second["target_version"] == "TEST-doctor_v3"
    await database.close()


async def test_runtime_activation_and_rollback_use_revisioned_exact_pointers(tmp_path, monkeypatch):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'runtime.sqlite3'}")
    await database.migrate()
    profiles = StyleProfileRepository(database)
    await profiles.create_profile("TEST-style")
    candidate = await profiles.append_version(
        profile_id="TEST-style",
        version="TEST-style_v1",
        display_name="TEST style",
        description="TEST expression only",
        tone_rules=("TEST concise",),
        source_corpus_sha256="a" * 64,
        status="evaluated",
    )

    async def published(repository, version):
        asset = await repository.get_profile_asset(version)
        assert asset is not None
        return StyleProfileSnapshot(profile=asset.to_profile(), examples=asset.examples)

    monkeypatch.setattr(StyleProfileRepository, "require_published", published)
    runtime = StyleRuntimeService(database, fallback_version="slimguard_default_v1")
    activated = await runtime.activate(
        StyleActivationAction(
            version=candidate.version,
            expected_revision=0,
            reason="TEST activate reviewed candidate",
        ),
        actor="TEST-admin",
    )
    assert activated["runtime"]["active_profile_version"] == "TEST-style_v1"
    assert activated["runtime"]["previous_profile_version"] == "slimguard_default_v1"
    assert activated["runtime"]["revision"] == 1
    assert (
        await StyleRuntimeVersionResolver(database, fallback_version="fallback").resolve()
        == "TEST-style_v1"
    )

    rolled_back = await runtime.rollback(
        StyleRollbackAction(
            expected_revision=1,
            reason="TEST restore previous exact version",
        ),
        actor="TEST-admin",
    )
    assert rolled_back["runtime"]["active_profile_version"] == "slimguard_default_v1"
    assert rolled_back["runtime"]["revision"] == 2
    async with database.session() as session:
        events = tuple(
            await session.scalars(
                select(StyleActivationEventRecord).order_by(
                    StyleActivationEventRecord.runtime_revision
                )
            )
        )
    assert [event.action for event in events] == ["activate", "rollback"]
    with pytest.raises(IntegrityError, match="append-only"):
        async with database.engine.begin() as connection:
            await connection.execute(text("UPDATE style_activation_events SET actor = 'tampered'"))
    await database.close()
