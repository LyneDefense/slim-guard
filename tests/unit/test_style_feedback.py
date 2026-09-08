"""Persistence tests use invented feedback only; no real conversation data is included."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)


def feedback(**changes) -> StyleCorrectionFeedbackInput:
    values = {
        "profile_version": "TEST-style-v2",
        "communication_act": "explain",
        "scenario": "TEST 用户只有三天的合成记录，询问能否判断长期趋势。",
        "user_message": "TEST 现在能看出长期趋势了吗？",
        "agent_response": "TEST 当前信息有限，建议继续记录。",
        "desired_response": "TEST 只有三天记录，判断不了长期趋势。",
        "guidance_note": "TEST 去掉正式铺垫，直接说明记录不足。",
        "deidentified_confirmed": True,
        "expression_only_confirmed": True,
    }
    values.update(changes)
    return StyleCorrectionFeedbackInput.model_validate(values)


@pytest.fixture
async def ledger(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'feedback.sqlite3'}")
    await database.migrate()
    try:
        yield database, StyleCorrectionFeedbackRepository(database)
    finally:
        await database.close()


async def test_feedback_is_named_hashed_and_filterable_without_requiring_a_known_act(ledger):
    _, repository = ledger
    first = await repository.append(
        feedback(),
        actor=" TEST-admin ",
        created_at=datetime(2026, 9, 8, 8, tzinfo=UTC),
    )
    second = await repository.append(
        feedback(
            profile_version="TEST-style-v3",
            communication_act=None,
            scenario="TEST 十二条之外的新场景。",
            agent_response="TEST 当前回复。",
            desired_response="TEST 期望回复。",
        ),
        actor="TEST-admin",
        created_at=datetime(2026, 9, 8, 9, tzinfo=UTC),
    )
    assert first["actor"] == "TEST-admin"
    assert len(first["content_sha256"]) == 64
    assert second["communication_act"] is None
    listing = await repository.list(limit=1)
    assert listing["total"] == 2
    assert listing["items"][0]["feedback_id"] == second["feedback_id"]
    filtered = await repository.list(
        profile_version="TEST-style-v2", communication_act="explain"
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["feedback_id"] == first["feedback_id"]


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
async def test_database_blocks_feedback_update_and_delete(ledger, operation):
    database, repository = ledger
    item = await repository.append(feedback(), actor="TEST-admin")
    statement = (
        "UPDATE style_correction_feedback SET actor = 'tampered' WHERE id = :id"
        if operation == "UPDATE"
        else "DELETE FROM style_correction_feedback WHERE id = :id"
    )
    with pytest.raises(IntegrityError, match="append-only"):
        async with database.engine.begin() as connection:
            await connection.execute(text(statement), {"id": item["feedback_id"]})
    assert (await repository.list())["items"][0]["actor"] == "TEST-admin"


@pytest.mark.parametrize(
    "changes",
    [
        {"desired_response": "TEST 当前信息有限，建议继续记录。"},
        {"deidentified_confirmed": False},
        {"expression_only_confirmed": False},
        {"scenario": " "},
        {"communication_act": "unknown"},
    ],
)
def test_feedback_requires_a_real_difference_privacy_review_and_valid_optional_act(changes):
    with pytest.raises(ValidationError):
        feedback(**changes)


async def test_naive_timestamps_and_invalid_actors_are_rejected_without_writes(ledger):
    _, repository = ledger
    with pytest.raises(ValueError):
        await repository.append(
            feedback(), actor="TEST-admin", created_at=datetime(2026, 9, 8)
        )
    with pytest.raises(ValueError):
        await repository.append(feedback(), actor=" ")
    assert (await repository.list())["total"] == 0


async def test_hash_binds_the_exact_corrected_content(ledger):
    _, repository = ledger
    created_at = datetime(2026, 9, 8, tzinfo=UTC)
    first = await repository.append(
        feedback(), actor="TEST-admin", created_at=created_at
    )
    second = await repository.append(
        feedback(desired_response="TEST 三天记录不能说明长期趋势。"),
        actor="TEST-admin",
        created_at=created_at + timedelta(seconds=1),
    )
    assert first["content_sha256"] != second["content_sha256"]
