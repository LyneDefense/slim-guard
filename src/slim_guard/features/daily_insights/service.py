"""Read-through scheduling and leased, restart-safe asynchronous generation."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast

from slim_guard.features.daily_insights.contracts import DailyInsightsView
from slim_guard.features.daily_insights.generation import (
    InsightGenerator,
    InsightRejected,
    reusable_headline,
)
from slim_guard.features.daily_insights.repository import InsightRepository
from slim_guard.features.daily_insights.snapshot import SnapshotReader

logger = logging.getLogger(__name__)


class DailyInsightsService:
    def __init__(
        self,
        snapshots: SnapshotReader,
        jobs: InsightRepository,
        generator: InsightGenerator | None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.snapshots, self.jobs, self.generator = snapshots, jobs, generator
        self.clock = clock or (lambda: datetime.now(UTC))

    async def get(
        self, user_id: str, *, now: datetime | None = None, retry: bool = False
    ) -> DailyInsightsView:
        now = now or self.clock()
        snapshot = await self.snapshots.read(user_id, now)
        if self.generator is None:
            return DailyInsightsView(
                date=snapshot.day,
                timezone=snapshot.timezone,
                status="unavailable",
                summary_state="unavailable",
            )
        job = await self.jobs.request(snapshot, now, retry=retry)
        ready = job.status == "ready" and job.previous is not None
        headline = reusable_headline(job, snapshot)
        output = job.previous if ready else None
        refs = (
            {ref for claim in output.summary.claims for ref in claim.source_refs}
            if output and output.summary
            else set()
        )
        return DailyInsightsView(
            date=snapshot.day,
            timezone=snapshot.timezone,
            status=cast(Literal["pending", "running", "ready", "failed"], job.status),
            headline=headline.text if headline else None,
            summary=output.summary.text if output and output.summary else None,
            summary_state=(
                "empty"
                if not snapshot.today_record_count
                else "ready"
                if ready
                else "failed"
                if job.status == "failed"
                else "pending"
            ),
            as_of=job.previous_snapshot.as_of if output and job.previous_snapshot else None,
            generated_at=job.generated_at if ready else None,
            evidence=tuple(e for e in snapshot.evidence if e.today and e.id in refs),
            failure_code=job.failure_code,
        )

    async def run_once(self, *, now: datetime | None = None) -> bool:
        now = now or self.clock()
        if self.generator is None:
            return False
        job = await self.jobs.claim(now)
        if job is None:
            return False
        try:
            output, review = await asyncio.wait_for(self.generator.generate(job), timeout=70)
            # Verify again after the model wait, even if no client polled during
            # a concurrent edit/delete. Never publish an obsolete assessment.
            current = await self.snapshots.read(job.snapshot.user_id, self.clock())
            if current.fingerprint != job.snapshot.fingerprint:
                await self.jobs.request(current, self.clock())
                await self.jobs.finish(job, self.clock(), failure_code="sources_changed")
            else:
                await self.jobs.finish(
                    job, self.clock(), output=output, review=review.model_dump_json()
                )
        except asyncio.CancelledError:
            # A cancelled worker leaves a lease; another process can recover it.
            raise
        except InsightRejected as exc:
            await self.jobs.finish(
                job, self.clock(), failure_code="review_failed", review=exc.review.model_dump_json()
            )
        except Exception as exc:
            logger.warning(
                "daily_insight_generation_failed", extra={"failure_type": type(exc).__name__}
            )
            await self.jobs.finish(job, self.clock(), failure_code="generation_failed")
        return True

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                logger.exception("daily_insight_worker_failed")
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
