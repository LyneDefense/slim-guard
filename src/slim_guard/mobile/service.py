from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from slim_guard.agent.runtime import (
    AgentRuntimeProtocol,
    AgentRuntimeRequest,
    AgentRuntimeResult,
)
from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    MobileAgentRequestRecord,
    MobileAuthIdentityRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.domain.body_fat.repository import BodyFatRepository
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.routine.contracts import RoutinePreferenceCommand, RoutineSetting
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemType
from slim_guard.harness.termination import HarnessTermination
from slim_guard.memory.repository import MemoryRepository
from slim_guard.mobile.contracts import (
    ChatHistoryView,
    ChatMessageView,
    ChatRequest,
    ChatResponse,
    MemoryView,
    MobileUserView,
    ProgressView,
    RoutineSettingRequest,
    RoutineUpdateRequest,
    RoutineView,
    TodayView,
    TrendPoint,
)
from slim_guard.observability.tracing import InteractionTraceRepository, bind_trace
from slim_guard.tools.contracts import ToolExecutionMode


class MobileServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MobileRequestClaim:
    id: str
    status: str
    request_hash: str
    turn_id: str | None
    final_text: str | None
    failure_code: str | None
    created: bool


class MobileApplicationService:
    def __init__(
        self,
        *,
        database: Database,
        runtime: AgentRuntimeProtocol | None,
        traces: InteractionTraceRepository,
        max_image_bytes: int = 10_485_760,
    ) -> None:
        self._database = database
        self._runtime = runtime
        self._traces = traces
        self._max_image_bytes = max_image_bytes
        self._memories = MemoryRepository(database)
        self._weights = WeightRepository(database)
        self._body_fat = BodyFatRepository(database)
        self._meals = MealRepository(database)
        self._exercise = ExerciseRepository(database)
        self._routines = RoutinePreferenceRepository(database)

    async def user(self, user_id: str) -> MobileUserView:
        async with self._database.session() as session:
            user = await session.get(SlimGuardUser, user_id)
            if user is None:
                raise MobileServiceError("user_not_found", "User was not found")
            identity = await session.scalar(
                select(MobileAuthIdentityRecord)
                .where(MobileAuthIdentityRecord.user_id == user_id)
                .order_by(MobileAuthIdentityRecord.created_at)
                .limit(1)
            )
            return MobileUserView(
                id=user.id,
                nickname=user.nickname,
                identity_hint=identity.display_hint if identity is not None else None,
                created_at=self._aware(user.created_at),
            )

    async def update_profile(self, user_id: str, *, nickname: str | None) -> MobileUserView:
        async with self._database.session() as session, session.begin():
            user = await session.get(SlimGuardUser, user_id)
            if user is None:
                raise MobileServiceError("user_not_found", "User was not found")
            user.nickname = nickname
            user.updated_at = datetime.now(UTC)
        return await self.user(user_id)

    async def chat(self, user_id: str, request: ChatRequest) -> ChatResponse:
        if self._runtime is None:
            raise MobileServiceError("agent_unavailable", "SlimGuard Agent is unavailable")
        image = self._decode_image(request.image_base64)
        request_hash = self._request_hash(request, image)
        claim = await self._claim_request(
            user_id=user_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
        )
        if claim.request_hash != request_hash:
            raise MobileServiceError(
                "idempotency_key_reused",
                "The idempotency key was already used for different content",
            )
        if not claim.created:
            return ChatResponse(
                request_id=claim.id,
                status=claim.status,
                turn_id=claim.turn_id,
                text=claim.final_text,
                failure_code=claim.failure_code,
                replayed=True,
            )

        trace_id = await self._traces.start_user_trace(
            user_id=user_id,
            trigger_type="user_message",
            channel_id="mobile",
            inbound_msgid=request.idempotency_key,
        )
        await self._attach_trace(claim.id, trace_id)
        try:
            with bind_trace(trace_id):
                result = await self._runtime.run_user_message(
                    AgentRuntimeRequest(
                        user_id=user_id,
                        text=request.text,
                        image_bytes=image,
                        image_mime_type=request.image_mime_type,
                        source_message_id=request.idempotency_key,
                        channel_id="mobile",
                        occurred_at=request.occurred_at,
                        execution_mode=ToolExecutionMode.LIVE,
                    )
                )
            await self._traces.attach_agent_turn(
                trace_id=trace_id,
                turn_id=result.turn_id,
                agent_version_id=result.agent_version_id,
            )
            if result.termination is HarnessTermination.FINAL_RESPONSE and result.final_text:
                final_text = result.final_text.strip()
                await self._complete_request(
                    claim.id,
                    status="succeeded",
                    turn_id=result.turn_id,
                    final_text=final_text,
                    failure_code=None,
                )
                await self._finish_trace(trace_id, result, succeeded=True)
                return ChatResponse(
                    request_id=claim.id,
                    status="succeeded",
                    turn_id=result.turn_id,
                    text=final_text,
                )
            if result.termination is HarnessTermination.WAITING_USER_CONFIRMATION:
                final_text = "这项操作需要你再次确认。确认执行，还是取消？"
                await self._complete_request(
                    claim.id,
                    status="succeeded",
                    turn_id=result.turn_id,
                    final_text=final_text,
                    failure_code=None,
                )
                await self._finish_trace(trace_id, result, succeeded=True)
                return ChatResponse(
                    request_id=claim.id,
                    status="succeeded",
                    turn_id=result.turn_id,
                    text=final_text,
                )
            failure_code = result.failure_code or result.termination.value
            await self._complete_request(
                claim.id,
                status="failed",
                turn_id=result.turn_id,
                final_text=None,
                failure_code=failure_code,
            )
            await self._finish_trace(trace_id, result, succeeded=False)
            return ChatResponse(
                request_id=claim.id,
                status="failed",
                turn_id=result.turn_id,
                failure_code=failure_code,
            )
        except Exception as exc:
            await self._complete_request(
                claim.id,
                status="failed",
                turn_id=None,
                final_text=None,
                failure_code="mobile_agent_failed",
            )
            await self._traces.mark_generation(
                trace_id=trace_id,
                status="failed",
                reply_kind="agent",
                failure_code="mobile_agent_failed",
                error_detail=type(exc).__name__,
            )
            await self._traces.mark_delivery(trace_id=trace_id, status="failed")
            raise MobileServiceError(
                "mobile_agent_failed", "SlimGuard could not complete this message"
            ) from exc

    async def chat_request(self, user_id: str, idempotency_key: str) -> ChatResponse:
        claim = await self._find_request(user_id, idempotency_key)
        if claim is None:
            raise MobileServiceError("request_not_found", "Chat request was not found")
        return ChatResponse(
            request_id=claim.id,
            status=claim.status,
            turn_id=claim.turn_id,
            text=claim.final_text,
            failure_code=claim.failure_code,
            replayed=True,
        )

    async def history(self, user_id: str, *, limit: int = 50) -> ChatHistoryView:
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .join(
                        AgentThreadRecord,
                        AgentThreadRecord.id == AgentItemRecord.thread_id,
                    )
                    .where(
                        AgentThreadRecord.user_id == user_id,
                        AgentItemRecord.item_type.in_(
                            (
                                ItemType.USER_MESSAGE.value,
                                ItemType.IMAGE_ATTACHMENT.value,
                                ItemType.AGENT_MESSAGE.value,
                            )
                        ),
                    )
                    .order_by(AgentItemRecord.created_at.desc(), AgentItemRecord.sequence.desc())
                    .limit(limit)
                )
            )
        items: list[ChatMessageView] = []
        for row in reversed(rows):
            payload = self._json(row.payload_json)
            if payload.get("redacted") is True:
                continue
            item_type = ItemType(row.item_type)
            role = "assistant" if item_type is ItemType.AGENT_MESSAGE else "user"
            kind = "image" if item_type is ItemType.IMAGE_ATTACHMENT else "text"
            text = payload.get("text")
            items.append(
                ChatMessageView(
                    id=row.id,
                    turn_id=row.turn_id,
                    role=role,
                    kind=kind,
                    text=text if isinstance(text, str) else None,
                    created_at=self._aware(row.created_at),
                )
            )
        return ChatHistoryView(items=items)

    async def memories(self, user_id: str) -> list[MemoryView]:
        now = datetime.now(UTC)
        rows = await self._memories.active(user_id, limit=100)
        return [
            MemoryView(
                id=row.id,
                key=row.key.value,
                kind=row.kind.value,
                value=row.value,
                stale=row.review_after is not None and self._aware(row.review_after) <= now,
                valid_from=self._aware(row.valid_from),
                review_after=(
                    self._aware(row.review_after) if row.review_after is not None else None
                ),
            )
            for row in rows
        ]

    async def routine(self, user_id: str) -> RoutineView:
        row = await self._routines.get(user_id)
        if row is None:
            return RoutineView(
                timezone="Asia/Shanghai",
                weight_reminder_time=None,
                meal_reminder_time=None,
                daily_review_time=None,
            )
        return RoutineView(
            timezone=row.timezone,
            weight_reminder_time=row.weight_reminder_time,
            meal_reminder_time=row.meal_reminder_time,
            daily_review_time=row.daily_review_time,
        )

    async def update_routine(
        self, user_id: str, request: RoutineUpdateRequest
    ) -> RoutineView:
        row = await self._routines.update(
            RoutinePreferenceCommand(
                user_id=user_id,
                timezone=request.timezone,
                weight=self._routine_setting(request.weight),
                meal=self._routine_setting(request.meal),
                daily_review=self._routine_setting(request.daily_review),
            )
        )
        return RoutineView(
            timezone=row.timezone,
            weight_reminder_time=row.weight_reminder_time,
            meal_reminder_time=row.meal_reminder_time,
            daily_review_time=row.daily_review_time,
        )

    async def progress(self, user_id: str, *, limit: int = 30) -> ProgressView:
        weights = await self._weights.recent_trend(user_id, limit=limit)
        body_fat = await self._body_fat.recent_trend(user_id, limit=limit)
        meals = await self._meals.recent(user_id, limit=limit)
        exercise = await self._exercise.recent(user_id, limit=limit)
        return ProgressView(
            weights=[
                TrendPoint(
                    id=row.id,
                    value=float(row.weight_kg),
                    occurred_at=self._aware(row.measured_at),
                )
                for row in reversed(weights.records)
            ],
            body_fat=[
                TrendPoint(
                    id=row.id,
                    value=float(row.percent),
                    occurred_at=self._aware(row.measured_at),
                )
                for row in reversed(body_fat.records)
            ],
            meals=[
                {
                    "id": row.id,
                    "meal_type": row.meal_type.value,
                    "foods": [food.model_dump(mode="json") for food in row.foods],
                    "note": row.note,
                    "occurred_at": self._aware(row.occurred_at),
                }
                for row in reversed(meals)
            ],
            exercise=[
                {
                    "id": row.id,
                    "activity_name": row.activity_name,
                    "duration_minutes": row.duration_minutes,
                    "steps": row.steps,
                    "distance_meters": row.distance_meters,
                    "reported_energy_kcal": row.reported_energy_kcal,
                    "note": row.note,
                    "occurred_at": self._aware(row.occurred_at),
                }
                for row in reversed(exercise)
            ],
        )

    async def today(self, user_id: str, *, now: datetime) -> TodayView:
        routine = await self.routine(user_id)
        local_date = self._aware(now).astimezone(ZoneInfo(routine.timezone)).date()
        progress = await self.progress(user_id, limit=30)
        meals_logged = sum(
            1
            for item in progress.meals
            if self._aware(item["occurred_at"]).astimezone(ZoneInfo(routine.timezone)).date()
            == local_date
        )
        exercise_logged = sum(
            1
            for item in progress.exercise
            if self._aware(item["occurred_at"]).astimezone(ZoneInfo(routine.timezone)).date()
            == local_date
        )
        return TodayView(
            date=local_date.isoformat(),
            current_weight_kg=(progress.weights[-1].value if progress.weights else None),
            current_body_fat_percent=(
                progress.body_fat[-1].value if progress.body_fat else None
            ),
            meals_logged=meals_logged,
            exercise_logged=exercise_logged,
            memories=await self.memories(user_id),
            routine=routine,
        )

    async def _claim_request(
        self, *, user_id: str, idempotency_key: str, request_hash: str
    ) -> MobileRequestClaim:
        row = MobileAgentRequestRecord(
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        async with self._database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return self._request_ref(row, created=True)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(MobileAgentRequestRecord).where(
                        MobileAgentRequestRecord.user_id == user_id,
                        MobileAgentRequestRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return self._request_ref(existing, created=False)

    async def _find_request(
        self, user_id: str, idempotency_key: str
    ) -> MobileRequestClaim | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(MobileAgentRequestRecord).where(
                    MobileAgentRequestRecord.user_id == user_id,
                    MobileAgentRequestRecord.idempotency_key == idempotency_key,
                )
            )
            return self._request_ref(row, created=False) if row is not None else None

    async def _attach_trace(self, request_id: str, trace_id: str) -> None:
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileAgentRequestRecord, request_id)
            if row is not None:
                row.trace_id = trace_id

    async def _complete_request(
        self,
        request_id: str,
        *,
        status: str,
        turn_id: str | None,
        final_text: str | None,
        failure_code: str | None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileAgentRequestRecord, request_id)
            if row is None:
                raise MobileServiceError("request_not_found", "Chat request was not found")
            row.status = status
            row.turn_id = turn_id
            row.final_text = final_text
            row.failure_code = failure_code
            row.completed_at = datetime.now(UTC)

    async def _finish_trace(
        self, trace_id: str, result: AgentRuntimeResult, *, succeeded: bool
    ) -> None:
        turn_id = result.turn_id
        termination = result.termination
        failure_code = result.failure_code
        await self._traces.record_event(
            trace_id=trace_id,
            component="agent",
            operation="turn_finished",
            status="completed" if succeeded else "failed",
            attributes={
                "turn_id": turn_id,
                "termination": termination.value,
                "failure_code": failure_code,
            },
            error_code=None if succeeded else failure_code,
        )
        await self._traces.mark_generation(
            trace_id=trace_id,
            status="succeeded" if succeeded else "failed",
            reply_kind="agent",
            failure_code=None if succeeded else failure_code,
        )
        await self._traces.mark_delivery(
            trace_id=trace_id,
            status="accepted" if succeeded else "failed",
        )

    def _decode_image(self, encoded: str | None) -> bytes | None:
        if encoded is None:
            return None
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MobileServiceError("invalid_image", "Image content is not valid base64") from exc
        if not content or len(content) > self._max_image_bytes:
            raise MobileServiceError("invalid_image_size", "Image size is not allowed")
        return content

    @staticmethod
    def _request_hash(request: ChatRequest, image: bytes | None) -> str:
        payload = {
            "text": request.text,
            "image_sha256": hashlib.sha256(image).hexdigest() if image else None,
            "image_mime_type": request.image_mime_type,
            "occurred_at": request.occurred_at.isoformat() if request.occurred_at else None,
        }
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _routine_setting(value: RoutineSettingRequest | None) -> RoutineSetting | None:
        if value is None:
            return None
        return RoutineSetting(
            enabled=value.enabled,
            local_time=value.local_time,
        )

    @staticmethod
    def _request_ref(
        row: MobileAgentRequestRecord, *, created: bool
    ) -> MobileRequestClaim:
        return MobileRequestClaim(
            id=row.id,
            status=row.status,
            request_hash=row.request_hash,
            turn_id=row.turn_id,
            final_text=row.final_text,
            failure_code=row.failure_code,
            created=created,
        )

    @staticmethod
    def _json(value: str) -> dict[str, object]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _aware(value: object) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("Expected datetime")
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)
