from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.errors import ImageAssetCollision
from slim_guard.domain.assets.repository import ImageAssetRepository

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\nimage-content"


async def prepare_repository(tmp_path: Path) -> tuple[Database, ImageAssetRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'assets.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            (
                SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW),
                SlimGuardUser(id="user-2", first_seen_at=NOW, last_seen_at=NOW),
            )
        )
    return database, ImageAssetRepository(database)


def command(*, content: bytes = PNG, user_id: str = "user-1") -> SaveImageAssetCommand:
    return SaveImageAssetCommand(
        user_id=user_id,
        content=content,
        declared_mime_type="image/png",
        channel_id="default",
        source_message_id="message-1",
        expires_at=NOW + timedelta(days=7),
    )


async def test_asset_is_idempotent_and_scoped_to_owner(tmp_path: Path) -> None:
    database, repository = await prepare_repository(tmp_path)
    try:
        first = await repository.save(command())
        repeated = await repository.save(command())
        loaded = await repository.get_for_user(
            first.asset.id,
            user_id="user-1",
            at=NOW,
        )
        foreign = await repository.get_for_user(
            first.asset.id,
            user_id="user-2",
            at=NOW,
        )

        assert first.created is True
        assert repeated.created is False
        assert repeated.asset == first.asset
        assert loaded is not None
        assert loaded.content == PNG
        assert loaded.ref.mime_type == "image/png"
        assert loaded.ref.size_bytes == len(PNG)
        assert foreign is None
    finally:
        await database.close()


async def test_same_source_cannot_be_replaced_with_other_content(tmp_path: Path) -> None:
    database, repository = await prepare_repository(tmp_path)
    try:
        await repository.save(command())

        with pytest.raises(ImageAssetCollision, match="source identity collision"):
            await repository.save(command(content=b"\x89PNG\r\n\x1a\nchanged"))
    finally:
        await database.close()


async def test_expired_asset_is_unreadable_and_can_be_purged(tmp_path: Path) -> None:
    database, repository = await prepare_repository(tmp_path)
    try:
        created = await repository.save(command())

        assert (
            await repository.get_for_user(
                created.asset.id,
                user_id="user-1",
                at=NOW + timedelta(days=8),
            )
            is None
        )
        assert await repository.purge_expired(at=NOW + timedelta(days=8)) == 1
    finally:
        await database.close()


def test_asset_rejects_spoofed_or_unrecognized_image_content() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        command(content=b"\xff\xd8\xffjpeg")
    with pytest.raises(ValidationError, match="Unsupported"):
        command(content=b"not-an-image")
