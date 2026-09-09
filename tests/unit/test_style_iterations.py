"""Durable style iteration control-plane tests use invented feedback only."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import StyleIterationInputRecord
from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)
from slim_guard.style_iterations import (
    StyleIterationConflict,
    StyleIterationCreate,
    StyleIterationRepository,
)


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

    claimed = await repository.claim_next(
        worker_id="TEST-worker", lease=timedelta(seconds=30)
    )
    assert claimed is not None and claimed["status"] == "validating"
    frozen = await repository.freeze_inputs(first["run_id"])
    assert frozen["hashes"]["source_sha256"]
    assert frozen["artifacts"]["input_snapshot"]["counts"]["style_corrections"] == 1
    await repository.store_classified_inputs(
        first["run_id"],
        classifications=(
            {
                "source_kind": "style_feedback",
                "source_id": feedback["feedback_id"],
                "classification": "style_existing_act",
                "summary": "TEST 可归入解释行为。",
                "derived_case_ids": ["TEST-new-case"],
            },
        ),
    )
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
                    "UPDATE style_iteration_inputs SET classification = 'tampered' "
                    "WHERE id = :id"
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
    cancelled = await repository.cancel(
        first["run_id"], actor="TEST-admin", reason="TEST 主动停止"
    )
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
