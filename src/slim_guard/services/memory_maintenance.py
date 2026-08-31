from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from slim_guard.memory.lifecycle import (
    MemoryLifecycleRepository,
    MemoryLifecycleResult,
    TranscriptScrubResult,
)

logger = logging.getLogger(__name__)


class MemoryMaintenanceService:
    """Applies configured privacy retention without deleting audit identities."""

    def __init__(
        self,
        *,
        lifecycle: MemoryLifecycleRepository,
        transcript_retention: timedelta = timedelta(days=30),
        revoked_value_retention: timedelta = timedelta(days=30),
        interval_seconds: int = 21_600,
        batch_size: int = 500,
        max_batches_per_run: int = 20,
    ) -> None:
        if transcript_retention <= timedelta(0):
            raise ValueError("Transcript retention must be positive")
        if revoked_value_retention < timedelta(0):
            raise ValueError("Revoked memory value retention cannot be negative")
        if interval_seconds < 60:
            raise ValueError("Memory maintenance interval must be at least 60 seconds")
        if not 1 <= batch_size <= 5000:
            raise ValueError("Memory maintenance batch size must be between 1 and 5000")
        if not 1 <= max_batches_per_run <= 100:
            raise ValueError("Memory maintenance max batches must be between 1 and 100")
        self._lifecycle = lifecycle
        self._transcript_retention = transcript_retention
        self._revoked_value_retention = revoked_value_retention
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._max_batches_per_run = max_batches_per_run

    async def run_once(self, *, now: datetime | None = None) -> MemoryLifecycleResult:
        reference_time = now or datetime.now(UTC)
        if reference_time.utcoffset() is None:
            raise ValueError("Memory maintenance time must be timezone-aware")
        item_count = 0
        execution_count = 0
        action_count = 0
        outbound_count = 0
        proactive_count = 0
        for _ in range(self._max_batches_per_run):
            batch = await self._lifecycle.scrub_transcript_bodies(
                before=reference_time - self._transcript_retention,
                redacted_at=reference_time,
                limit=self._batch_size,
            )
            item_count += batch.item_count
            execution_count += batch.tool_execution_count
            action_count += batch.pending_action_count
            outbound_count += batch.outbound_message_count
            proactive_count += batch.proactive_message_count
            if (
                max(
                    batch.item_count,
                    batch.tool_execution_count,
                    batch.pending_action_count,
                    batch.outbound_message_count,
                    batch.proactive_message_count,
                )
                < self._batch_size
            ):
                break
        revoked_count = await self._lifecycle.purge_revoked_values(
            before=reference_time - self._revoked_value_retention,
        )
        expired_facts, expired_handoffs = await self._lifecycle.expire_due(at=reference_time)
        result = MemoryLifecycleResult(
            transcript=TranscriptScrubResult(
                item_count=item_count,
                tool_execution_count=execution_count,
                pending_action_count=action_count,
                outbound_message_count=outbound_count,
                proactive_message_count=proactive_count,
            ),
            revoked_value_count=revoked_count,
            expired_fact_count=expired_facts,
            expired_handoff_count=expired_handoffs,
        )
        if any(
            (
                item_count,
                execution_count,
                action_count,
                outbound_count,
                proactive_count,
                revoked_count,
                expired_facts,
                expired_handoffs,
            )
        ):
            logger.info(
                "memory_privacy_maintenance_completed",
                extra={
                    "transcript_item_count": item_count,
                    "tool_execution_count": execution_count,
                    "pending_action_count": action_count,
                    "outbound_message_count": outbound_count,
                    "proactive_message_count": proactive_count,
                    "revoked_value_count": revoked_count,
                    "expired_fact_count": expired_facts,
                    "expired_handoff_count": expired_handoffs,
                },
            )
        return result

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("memory_privacy_maintenance_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass
