from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.services.maintenance import AssetMaintenanceService

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + b"asset-bytes"


async def test_asset_maintenance_physically_purges_expired_images(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'maintenance.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    assets = ImageAssetRepository(database)
    expired = await assets.save(
        SaveImageAssetCommand(
            user_id="user-1",
            content=PNG,
            declared_mime_type="image/png",
            channel_id="default",
            source_message_id="image-1",
            expires_at=NOW + timedelta(minutes=1),
        )
    )
    try:
        deleted = await AssetMaintenanceService(
            assets=assets,
            interval_seconds=60,
        ).run_once(now=NOW + timedelta(minutes=2))
        loaded = await assets.get_for_user(
            expired.asset.id,
            user_id="user-1",
            at=NOW,
        )

        assert deleted == 1
        assert loaded is None
    finally:
        await database.close()
