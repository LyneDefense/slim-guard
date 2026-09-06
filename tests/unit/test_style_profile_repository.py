from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select

from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleProfile
from slim_guard.db.models import StyleProfileRecord, StyleProfileVersionRecord
from slim_guard.db.session import Database
from slim_guard.style_profiles import (
    DEFAULT_STYLE_PROFILE_PROMPT,
    StyleProfileAlreadyExists,
    StyleProfileRepository,
)


async def test_migration_seeds_the_active_default_style_profile(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'style-default.sqlite3'}")
    await database.create_schema()
    repository = StyleProfileRepository(database)
    try:
        profile = await repository.get_default()
        by_version = await repository.get_profile("slimguard_default_v1")
        asset = await repository.get_profile_asset("slimguard_default_v1")

        assert profile == SLIMGUARD_DEFAULT_V1
        assert by_version == SLIMGUARD_DEFAULT_V1
        assert isinstance(profile, StyleProfile)
        assert asset is not None
        assert asset.prompt_sha256 == hashlib.sha256(
            DEFAULT_STYLE_PROFILE_PROMPT.encode()
        ).hexdigest()
        assert asset.status == "active"
        async with database.session() as session:
            assert await session.scalar(
                select(func.count(StyleProfileRecord.id))
            ) == 1
            assert await session.scalar(
                select(func.count(StyleProfileVersionRecord.id))
            ) == 1
    finally:
        await database.close()


async def test_versions_are_append_only_and_active_alias_is_explicit(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'style-version.sqlite3'}")
    await database.create_schema()
    repository = StyleProfileRepository(database)
    style_spec = {
        "display_name": "严格教练",
        "description": "直接、克制，但不羞辱用户。",
        "tone_rules": ["先说结论", "一次只给两个行动"],
        "prohibited_phrases": ["你太懒了"],
        "preferred_max_paragraphs": 3,
    }
    try:
        await repository.create_profile("strict_coach")
        first = await repository.append_version(
            profile_id="strict_coach",
            version="strict_coach_v1",
            style_spec=style_spec,
            prompt_text="Keep the meaning unchanged.",
            status="evaluated",
        )
        assert await repository.get_active("strict_coach") is None

        activated = await repository.activate(
            profile_id="strict_coach",
            version="strict_coach_v1",
        )
        style_spec["tone_rules"].append("caller mutation")
        resolved = await repository.get_active("strict_coach")

        assert resolved is not None
        assert resolved.version == first.version == activated.version
        assert resolved.tone_rules == ("先说结论", "一次只给两个行动")
        with pytest.raises(StyleProfileAlreadyExists, match="cannot be overwritten"):
            await repository.append_version(
                profile_id="strict_coach",
                version="strict_coach_v1",
                style_spec={
                    **style_spec,
                    "tone_rules": ["尝试覆盖旧版本"],
                },
            )
    finally:
        await database.close()


async def test_prompt_digest_must_match_supplied_prompt(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'style-digest.sqlite3'}")
    await database.create_schema()
    repository = StyleProfileRepository(database)
    try:
        await repository.create_profile("profile")
        with pytest.raises(ValueError, match="does not match"):
            await repository.append_version(
                profile_id="profile",
                version="profile_v1",
                style_spec={
                    "display_name": "测试风格",
                    "description": "测试",
                    "tone_rules": ["清晰"],
                    "prohibited_phrases": [],
                    "preferred_max_paragraphs": 2,
                },
                prompt_text="prompt",
                prompt_sha256="0" * 64,
            )
    finally:
        await database.close()
