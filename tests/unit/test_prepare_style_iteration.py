"""Iteration snapshots use invented corrections and never call a model."""

from __future__ import annotations

from argparse import Namespace

import pytest

from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)
from slim_guard.tools.prepare_style_iteration import prepare


def arguments(database_url: str, **changes) -> Namespace:
    values = {
        "database_url": database_url,
        "source_profile_version": "TEST-style-v2",
        "target_profile_version": "TEST-style-v3",
        "actor": "TEST-operator",
        "confirm_reviewed_inputs": True,
    }
    values.update(changes)
    return Namespace(**values)


async def add_feedback(database: Database) -> None:
    await StyleCorrectionFeedbackRepository(database).append(
        StyleCorrectionFeedbackInput(
            profile_version="TEST-style-v2",
            communication_act=None,
            scenario="TEST 合成新场景。",
            user_message="TEST 用户消息。",
            agent_response="TEST 当前回复。",
            desired_response="TEST 期望回复。",
            deidentified_confirmed=True,
            expression_only_confirmed=True,
        ),
        actor="TEST-admin",
    )


async def test_prepare_freezes_named_corrections_as_a_non_published_version_input(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'iteration.sqlite3'}"
    database = Database(url)
    await database.migrate()
    await add_feedback(database)
    await database.close()

    first = await prepare(arguments(url))
    second = await prepare(arguments(url))
    assert first["status"] == "prepared_pending_profile_revision"
    assert first["source_profile_version"] == "TEST-style-v2"
    assert first["target_profile_version"] == "TEST-style-v3"
    assert first["counts"] == {
        "reviewed_a_b_cases": 0,
        "accepted_a_b_cases": 0,
        "rejected_a_b_cases": 0,
        "style_corrections": 1,
    }
    assert first["sources"]["style_corrections"][0]["actor"] == "TEST-admin"
    assert first["source_sha256"] == second["source_sha256"]
    assert first["published"] is False
    assert first["activated"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"confirm_reviewed_inputs": False},
        {"actor": " "},
        {"target_profile_version": "TEST-style-v2"},
    ],
)
async def test_prepare_requires_explicit_review_and_a_new_target_version(tmp_path, changes):
    url = f"sqlite+aiosqlite:///{tmp_path / 'invalid.sqlite3'}"
    with pytest.raises(ValueError):
        await prepare(arguments(url, **changes))


async def test_prepare_refuses_to_manufacture_a_version_without_revision_input(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'empty.sqlite3'}"
    with pytest.raises(ValueError, match="No rejected review or named correction"):
        await prepare(arguments(url))
