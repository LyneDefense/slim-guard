from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import ImageAssetRecord
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import (
    ImageAsset,
    ImageAssetCreation,
    ImageAssetRef,
    SaveImageAssetCommand,
)
from slim_guard.domain.assets.errors import ImageAssetCollision


class ImageAssetRepository:
    """Short-lived, user-scoped storage for model-inspectable image bytes."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def save(self, command: SaveImageAssetCommand) -> ImageAssetCreation:
        content_sha256 = hashlib.sha256(command.content).hexdigest()
        row = ImageAssetRecord(
            user_id=command.user_id,
            content=command.content,
            content_sha256=content_sha256,
            mime_type=command.mime_type,
            size_bytes=len(command.content),
            channel_id=command.channel_id,
            source_message_id=command.source_message_id,
            expires_at=command.expires_at.astimezone(UTC),
        )
        async with self.database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return ImageAssetCreation(asset=self._ref(row), created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(ImageAssetRecord).where(
                        ImageAssetRecord.channel_id == command.channel_id,
                        ImageAssetRecord.source_message_id == command.source_message_id,
                    )
                )
                if existing is None:
                    raise ImageAssetCollision(
                        "Image asset conflicted with an unknown persisted row"
                    ) from None
                self._assert_same_asset(existing, command, content_sha256)
                return ImageAssetCreation(asset=self._ref(existing), created=False)

    async def get_for_user(
        self,
        asset_id: str,
        *,
        user_id: str,
        at: datetime,
    ) -> ImageAsset | None:
        if at.utcoffset() is None:
            raise ValueError("Image asset read time must be timezone-aware")
        async with self.database.session() as session:
            row = await session.scalar(
                select(ImageAssetRecord).where(
                    ImageAssetRecord.id == asset_id,
                    ImageAssetRecord.user_id == user_id,
                    ImageAssetRecord.expires_at > at.astimezone(UTC),
                )
            )
            return ImageAsset(ref=self._ref(row), content=row.content) if row else None

    async def purge_expired(self, *, at: datetime) -> int:
        if at.utcoffset() is None:
            raise ValueError("Image asset purge time must be timezone-aware")
        async with self.database.session() as session, session.begin():
            result = await session.execute(
                delete(ImageAssetRecord).where(
                    ImageAssetRecord.expires_at <= at.astimezone(UTC)
                )
            )
            return int(cast(CursorResult[Any], result).rowcount or 0)

    @classmethod
    def _assert_same_asset(
        cls,
        row: ImageAssetRecord,
        command: SaveImageAssetCommand,
        content_sha256: str,
    ) -> None:
        expected = (
            command.user_id,
            content_sha256,
            command.mime_type,
            len(command.content),
            command.channel_id,
            command.source_message_id,
            command.expires_at.astimezone(UTC),
        )
        actual = (
            row.user_id,
            row.content_sha256,
            row.mime_type,
            row.size_bytes,
            row.channel_id,
            row.source_message_id,
            cls._as_utc(row.expires_at),
        )
        if actual != expected:
            raise ImageAssetCollision(
                f"Image source identity collision: {command.channel_id}/"
                f"{command.source_message_id}"
            )

    @classmethod
    def _ref(cls, row: ImageAssetRecord) -> ImageAssetRef:
        return ImageAssetRef(
            id=row.id,
            user_id=row.user_id,
            content_sha256=row.content_sha256,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            channel_id=row.channel_id,
            source_message_id=row.source_message_id,
            created_at=cls._as_utc(row.created_at),
            expires_at=cls._as_utc(row.expires_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
