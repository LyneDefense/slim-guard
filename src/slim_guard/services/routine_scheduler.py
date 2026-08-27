from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from slim_guard.agent.runtime import (
    AgentRuntimeProtocol,
    AgentScheduledRequest,
)
from slim_guard.db.repositories import ConversationRef
from slim_guard.domain.routine.contracts import ReminderKind
from slim_guard.domain.routine.jobs import (
    RoutineJobPlanner,
    RoutineJobRef,
    RoutineJobRepository,
    RoutineJobStatus,
)
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.termination import HarnessTermination
from slim_guard.integrations.wecom_kf.client import WeComClientProtocol
from slim_guard.integrations.wecom_kf.errors import WeComAPIError, WeComTransportError
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.proactive_delivery import (
    ProactiveDeliveryPolicy,
    ProactiveDeliveryRepository,
    ProactiveDeliveryStatus,
)

logger = logging.getLogger(__name__)


class ConversationControl(Protocol):
    async def ensure_agent_control(
        self,
        conversation: ConversationRef,
    ) -> WeComServiceState | None: ...


class RoutineSchedulerService:
    """Plans, runs, and delivers durable user-local reminder and review jobs."""

    def __init__(
        self,
        *,
        planner: RoutineJobPlanner,
        jobs: RoutineJobRepository,
        preferences: RoutinePreferenceRepository,
        checkins: DailyCheckinStatusRepository,
        policy: ProactiveDeliveryPolicy,
        deliveries: ProactiveDeliveryRepository,
        runtime: AgentRuntimeProtocol,
        client: WeComClientProtocol,
        conversation_control: ConversationControl,
        interval_seconds: int = 30,
        job_lease: timedelta = timedelta(minutes=2),
        send_retry_after: timedelta = timedelta(minutes=2),
        max_lateness: timedelta = timedelta(hours=2),
        agent_timeout: timedelta = timedelta(seconds=45),
        max_attempts: int = 3,
        max_message_chars: int = 1500,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("Routine scheduler interval must be positive")
        if max_attempts < 1:
            raise ValueError("Routine scheduler attempts must be positive")
        self._planner = planner
        self._jobs = jobs
        self._preferences = preferences
        self._checkins = checkins
        self._policy = policy
        self._deliveries = deliveries
        self._runtime = runtime
        self._client = client
        self._conversation_control = conversation_control
        self._interval_seconds = interval_seconds
        self._job_lease = job_lease
        self._send_retry_after = send_retry_after
        self._max_lateness = max_lateness
        self._agent_timeout = agent_timeout
        self._max_attempts = max_attempts
        self._max_message_chars = max_message_chars

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("routine_scheduler_cycle_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self, *, now: datetime | None = None) -> int:
        reference_time = now or datetime.now(UTC)
        if reference_time.utcoffset() is None:
            raise ValueError("Routine scheduler time must be timezone-aware")
        await self._planner.plan_due(now=reference_time)
        claimed = await self._jobs.claim_due(
            now=reference_time,
            lease_duration=self._job_lease,
        )
        completed = 0
        for job in claimed:
            try:
                await self._handle(job, now=reference_time)
            except Exception:
                logger.exception(
                    "routine_job_attempt_failed",
                    extra={
                        "job_id": job.id,
                        "job_kind": job.kind.value,
                        "attempt_count": job.attempt_count,
                        "user_ref": self._user_ref(job.user_id),
                    },
                )
                if job.attempt_count >= self._max_attempts:
                    await self._jobs.finish(
                        job_id=job.id,
                        status=RoutineJobStatus.FAILED,
                        result_code="attempts_exhausted",
                        completed_at=reference_time,
                    )
                continue
            completed += 1
        return completed

    async def _handle(self, job: RoutineJobRef, *, now: datetime) -> None:
        if now - job.scheduled_for > self._max_lateness:
            await self._finish_skipped(job, "stale_schedule", now)
            return
        preference = await self._preferences.get(job.user_id)
        if preference is None or preference.time_for(job.kind) is None:
            await self._finish_skipped(job, "routine_disabled", now)
            return
        checkins = await self._checkins.get(
            user_id=job.user_id,
            local_date=job.local_date,
            timezone=preference.timezone,
        )
        if job.kind is ReminderKind.WEIGHT and checkins.has_weight:
            await self._finish_skipped(job, "weight_already_recorded", now)
            return
        if job.kind is ReminderKind.MEAL and checkins.has_meal:
            await self._finish_skipped(job, "meal_already_recorded", now)
            return

        delivery = await self._deliveries.get(job.id)
        if delivery is None:
            eligibility = await self._policy.evaluate(user_id=job.user_id, now=now)
            if not eligibility.allowed or eligibility.route is None:
                await self._finish_skipped(job, eligibility.code, now)
                return
            route = eligibility.route
        else:
            route = delivery.route
        service_state = await self._conversation_control.ensure_agent_control(
            ConversationRef(
                channel_id=route.channel_id,
                open_kfid=route.open_kfid,
                external_userid=route.external_userid,
            )
        )
        if service_state is not WeComServiceState.SMART_ASSISTANT:
            await self._finish_skipped(job, "external_session_not_agent", now)
            return

        result_turn_id: str
        if delivery is None:
            result = await self._runtime.run_scheduled(
                AgentScheduledRequest(
                    user_id=job.user_id,
                    trigger=self._trigger(job.kind),
                    deadline_at=now + self._agent_timeout,
                )
            )
            if (
                result.termination is not HarnessTermination.FINAL_RESPONSE
                or result.final_text is None
                or not result.final_text.strip()
            ):
                await self._jobs.finish(
                    job_id=job.id,
                    status=RoutineJobStatus.FAILED,
                    result_code=result.failure_code or result.termination.value,
                    result_turn_id=result.turn_id,
                    completed_at=now,
                )
                return
            delivery = await self._deliveries.prepare(
                job_id=job.id,
                route=route,
                content=result.final_text.strip()[: self._max_message_chars],
                source_turn_id=result.turn_id,
            )
            result_turn_id = result.turn_id
        else:
            result_turn_id = delivery.source_turn_id

        if delivery.status is ProactiveDeliveryStatus.ACCEPTED:
            await self._jobs.finish(
                job_id=job.id,
                status=RoutineJobStatus.COMPLETED,
                result_code="delivered",
                result_turn_id=result_turn_id,
                completed_at=now,
            )
            return
        if delivery.status in {
            ProactiveDeliveryStatus.UNKNOWN,
            ProactiveDeliveryStatus.FAILED,
        }:
            await self._jobs.finish(
                job_id=job.id,
                status=RoutineJobStatus.FAILED,
                result_code=f"delivery_{delivery.status.value}",
                result_turn_id=result_turn_id,
                completed_at=now,
            )
            return
        if not await self._deliveries.claim(
            job_id=job.id,
            now=now,
            retry_after=self._send_retry_after,
        ):
            raise RuntimeError(f"Delivery is not claimable: {delivery.status.value}")
        try:
            await self._client.send_text(
                external_userid=route.external_userid,
                open_kfid=route.open_kfid,
                content=delivery.content,
                msgid=delivery.platform_msgid,
            )
        except WeComTransportError as exc:
            await self._deliveries.complete(
                job_id=job.id,
                status=ProactiveDeliveryStatus.UNKNOWN,
                last_error=type(exc).__name__,
                now=now,
            )
            await self._jobs.finish(
                job_id=job.id,
                status=RoutineJobStatus.FAILED,
                result_code="delivery_result_unknown",
                result_turn_id=result_turn_id,
                completed_at=now,
            )
        except WeComAPIError as exc:
            await self._deliveries.complete(
                job_id=job.id,
                status=ProactiveDeliveryStatus.FAILED,
                last_error=f"{exc.errcode}:{exc.errmsg}",
                now=now,
            )
            await self._jobs.finish(
                job_id=job.id,
                status=RoutineJobStatus.FAILED,
                result_code=f"wecom_api_error:{exc.errcode}",
                result_turn_id=result_turn_id,
                completed_at=now,
            )
        else:
            await self._deliveries.complete(
                job_id=job.id,
                status=ProactiveDeliveryStatus.ACCEPTED,
                now=now,
            )
            await self._jobs.finish(
                job_id=job.id,
                status=RoutineJobStatus.COMPLETED,
                result_code="delivered",
                result_turn_id=result_turn_id,
                completed_at=now,
            )
            logger.info(
                "routine_message_accepted",
                extra={
                    "job_id": job.id,
                    "job_kind": job.kind.value,
                    "user_ref": self._user_ref(job.user_id),
                },
            )

    async def _finish_skipped(
        self,
        job: RoutineJobRef,
        code: str,
        now: datetime,
    ) -> None:
        await self._jobs.finish(
            job_id=job.id,
            status=RoutineJobStatus.SKIPPED,
            result_code=code,
            completed_at=now,
        )
        logger.info(
            "routine_job_skipped",
            extra={
                "job_id": job.id,
                "job_kind": job.kind.value,
                "result_code": code,
                "user_ref": self._user_ref(job.user_id),
            },
        )

    @staticmethod
    def _trigger(kind: ReminderKind) -> TurnTrigger:
        return {
            ReminderKind.WEIGHT: TurnTrigger.WEIGHT_REMINDER,
            ReminderKind.MEAL: TurnTrigger.MEAL_REMINDER,
            ReminderKind.DAILY_REVIEW: TurnTrigger.DAILY_REVIEW,
        }[kind]

    @staticmethod
    def _user_ref(user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:12]
