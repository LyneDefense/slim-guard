from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from slim_guard.domain.assets.repository import ImageAssetRepository

logger = logging.getLogger(__name__)


class AssetMaintenanceService:
    """Periodically removes expired image bytes from short-lived storage."""

    def __init__(
        self,
        *,
        assets: ImageAssetRepository,
        interval_seconds: int = 21_600,
    ) -> None:
        if interval_seconds < 60:
            raise ValueError("Asset maintenance interval must be at least 60 seconds")
        self._assets = assets
        self._interval_seconds = interval_seconds

    async def run_once(self, *, now: datetime | None = None) -> int:
        reference_time = now or datetime.now(UTC)
        deleted = await self._assets.purge_expired(at=reference_time)
        if deleted:
            logger.info("expired_image_assets_purged", extra={"deleted_count": deleted})
        return deleted

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("image_asset_maintenance_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass
