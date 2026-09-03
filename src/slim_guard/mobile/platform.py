from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    AgentItemRedactionRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    BodyFatRecord,
    ChannelIdentity,
    ExerciseRecord,
    ImageAssetRecord,
    InboundMessage,
    InteractionTraceRecord,
    MealRecord,
    MemoryBulkOperationRecord,
    MemoryHandoffRecord,
    MemoryIndexOutboxRecord,
    MobileAgentRequestRecord,
    MobileAuthIdentityRecord,
    MobileDeviceRecord,
    MobileSessionRecord,
    MobileWeComBindingRecord,
    OutboundMessage,
    PendingActionRecord,
    ProactiveMessageRecord,
    RoutineJobRecord,
    SlimGuardUser,
    ToolExecutionRecord,
    TraceSpanRecord,
    UserMemoryEventRecord,
    UserMemoryFactRecord,
    UserRoutinePreference,
    WeComConversation,
    WeightRecord,
)
from slim_guard.db.session import Database
from slim_guard.memory.engine import MemoryEngine
from slim_guard.mobile.contracts import (
    DeviceRegistrationRequest,
    DeviceView,
    WeComBindingView,
)

_BINDING_PATTERN = re.compile(r"^SG-([A-HJ-NP-Z2-9]{8})$", re.IGNORECASE)
_BINDING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class MobilePlatformError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BindingClaimResult:
    status: str
    message: str


class MobilePlatformService:
    def __init__(
        self,
        *,
        database: Database,
        secret: str,
        binding_ttl: timedelta = timedelta(minutes=10),
        memory_engine: MemoryEngine | None = None,
    ) -> None:
        self._database = database
        self._key = secret.encode()
        self._binding_ttl = binding_ttl
        self._memory_engine = memory_engine

    async def register_device(
        self,
        user_id: str,
        request: DeviceRegistrationRequest,
        *,
        now: datetime,
    ) -> DeviceView:
        current = self._aware(now)
        async with self._database.session() as session, session.begin():
            if await session.get(SlimGuardUser, user_id) is None:
                raise MobilePlatformError("user_not_found", "User was not found")
            row = await session.scalar(
                select(MobileDeviceRecord).where(
                    MobileDeviceRecord.installation_id == request.installation_id
                )
            )
            token_owner = await session.scalar(
                select(MobileDeviceRecord).where(
                    MobileDeviceRecord.push_provider == request.push_provider,
                    MobileDeviceRecord.push_token == request.push_token,
                )
            )
            if token_owner is not None and (row is None or token_owner.id != row.id):
                token_owner.revoked_at = current
                token_owner.push_token = f"revoked:{token_owner.id}:{int(current.timestamp())}"
            if row is None:
                row = MobileDeviceRecord(
                    user_id=user_id,
                    installation_id=request.installation_id,
                    platform=request.platform,
                    push_provider=request.push_provider,
                    push_token=request.push_token,
                    created_at=current,
                    updated_at=current,
                    last_seen_at=current,
                )
                session.add(row)
            else:
                row.user_id = user_id
                row.platform = request.platform
                row.push_provider = request.push_provider
                row.push_token = request.push_token
                row.revoked_at = None
                row.updated_at = current
                row.last_seen_at = current
            row.app_version = request.app_version
            row.timezone = request.timezone
            row.locale = request.locale
            await session.flush()
            return self._device_view(row)

    async def revoke_device(self, user_id: str, device_id: str, *, now: datetime) -> None:
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileDeviceRecord, device_id)
            if row is None or row.user_id != user_id:
                raise MobilePlatformError("device_not_found", "Device was not found")
            row.revoked_at = self._aware(now)

    async def create_binding(self, user_id: str, *, now: datetime) -> WeComBindingView:
        current = self._aware(now)
        code = "".join(secrets.choice(_BINDING_ALPHABET) for _ in range(8))
        async with self._database.session() as session, session.begin():
            if await session.get(SlimGuardUser, user_id) is None:
                raise MobilePlatformError("user_not_found", "User was not found")
            existing = tuple(
                await session.scalars(
                    select(MobileWeComBindingRecord).where(
                        MobileWeComBindingRecord.mobile_user_id == user_id,
                        MobileWeComBindingRecord.status == "pending",
                    )
                )
            )
            for row in existing:
                row.status = "revoked"
            row = MobileWeComBindingRecord(
                mobile_user_id=user_id,
                code_hash=self._code_hash(code),
                code_hint=code[-4:],
                status="pending",
                expires_at=current + self._binding_ttl,
                created_at=current,
            )
            session.add(row)
            await session.flush()
            return self._binding_view(row, code=code)

    async def binding(self, user_id: str, *, now: datetime) -> WeComBindingView | None:
        current = self._aware(now)
        async with self._database.session() as session, session.begin():
            row = await session.scalar(
                select(MobileWeComBindingRecord)
                .where(MobileWeComBindingRecord.mobile_user_id == user_id)
                .order_by(MobileWeComBindingRecord.created_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            if row.status == "pending" and self._aware(row.expires_at) <= current:
                row.status = "expired"
            return self._binding_view(row)

    async def revoke_binding(self, user_id: str) -> None:
        async with self._database.session() as session, session.begin():
            rows = tuple(
                await session.scalars(
                    select(MobileWeComBindingRecord).where(
                        MobileWeComBindingRecord.mobile_user_id == user_id,
                        MobileWeComBindingRecord.status == "pending",
                    )
                )
            )
            for row in rows:
                row.status = "revoked"

    async def claim_wecom_message(
        self,
        *,
        channel_id: str,
        external_userid: str,
        text: str | None,
        now: datetime,
    ) -> BindingClaimResult | None:
        match = _BINDING_PATTERN.fullmatch((text or "").strip())
        if match is None:
            return None
        current = self._aware(now)
        code = match.group(1).upper()
        async with self._database.session() as session, session.begin():
            binding = await session.scalar(
                select(MobileWeComBindingRecord).where(
                    MobileWeComBindingRecord.code_hash == self._code_hash(code)
                )
            )
            if binding is None or binding.status != "pending":
                return BindingClaimResult(
                    "invalid",
                    "这个绑定码无效或已经使用过，请回 App 重新生成。",
                )
            if self._aware(binding.expires_at) <= current:
                binding.status = "expired"
                return BindingClaimResult("expired", "这个绑定码已经过期，请回 App 重新生成。")
            identity = await session.get(
                ChannelIdentity,
                {"channel_id": channel_id, "external_userid": external_userid},
            )
            if identity is None:
                return BindingClaimResult(
                    "invalid",
                    "当前微信身份还没有准备好，请稍后重新发送绑定码。",
                )
            source_id = binding.mobile_user_id
            target_id = identity.user_id
            if source_id != target_id:
                source_thread = await session.scalar(
                    select(AgentThreadRecord.id).where(AgentThreadRecord.user_id == source_id)
                )
                target_thread = await session.scalar(
                    select(AgentThreadRecord.id).where(AgentThreadRecord.user_id == target_id)
                )
                if source_thread is not None and target_thread is not None:
                    binding.status = "conflict"
                    return BindingClaimResult(
                        "conflict",
                        "App 和微信两边都已有记录。为了不覆盖你的数据，"
                        "这次没有自动合并，请联系管理员处理。",
                    )
                if source_thread is None:
                    await self._move_mobile_account(session, source_id, target_id)
                    binding.mobile_user_id = target_id
                    binding.target_user_id = target_id
                    await session.flush()
                    source = await session.get(SlimGuardUser, source_id)
                    if source is not None:
                        await session.delete(source)
                else:
                    identity.user_id = source_id
                    binding.target_user_id = source_id
                    target = await session.get(SlimGuardUser, target_id)
                    source = await session.get(SlimGuardUser, source_id)
                    if source is not None and target is not None and source.nickname is None:
                        source.nickname = target.nickname
                    await session.flush()
                    if target is not None:
                        await session.delete(target)
            else:
                binding.target_user_id = target_id
            binding.status = "claimed"
            binding.channel_id = channel_id
            binding.external_user_ref = hashlib.sha256(external_userid.encode()).hexdigest()[:16]
            binding.claimed_at = current
            return BindingClaimResult(
                "claimed",
                "绑定成功。以后在 App 和微信里，我看到的是同一份记录与记忆。",
            )

    async def delete_account(self, user_id: str) -> None:
        if self._memory_engine is not None:
            await self._memory_engine.delete_user(user_id=user_id)
        async with self._database.session() as session, session.begin():
            user = await session.get(SlimGuardUser, user_id)
            if user is None:
                raise MobilePlatformError("user_not_found", "User was not found")
            thread_ids = tuple(
                await session.scalars(
                    select(AgentThreadRecord.id).where(AgentThreadRecord.user_id == user_id)
                )
            )
            turn_ids = (
                tuple(
                    await session.scalars(
                        select(AgentTurnRecord.id).where(AgentTurnRecord.thread_id.in_(thread_ids))
                    )
                )
                if thread_ids
                else ()
            )
            item_ids = (
                tuple(
                    await session.scalars(
                        select(AgentItemRecord.id).where(AgentItemRecord.thread_id.in_(thread_ids))
                    )
                )
                if thread_ids
                else ()
            )
            trace_ids = tuple(
                await session.scalars(
                    select(InteractionTraceRecord.id).where(
                        InteractionTraceRecord.user_id == user_id
                    )
                )
            )
            routine_job_ids = tuple(
                await session.scalars(
                    select(RoutineJobRecord.id).where(RoutineJobRecord.user_id == user_id)
                )
            )
            identities = tuple(
                await session.scalars(
                    select(ChannelIdentity).where(ChannelIdentity.user_id == user_id)
                )
            )

            if item_ids:
                await session.execute(
                    delete(AgentItemRedactionRecord).where(
                        AgentItemRedactionRecord.item_id.in_(item_ids)
                    )
                )
            if trace_ids:
                await session.execute(
                    delete(TraceSpanRecord).where(TraceSpanRecord.trace_id.in_(trace_ids))
                )
            if routine_job_ids:
                await session.execute(
                    delete(ProactiveMessageRecord).where(
                        ProactiveMessageRecord.job_id.in_(routine_job_ids)
                    )
                )

            for model in (
                MobileAgentRequestRecord,
                MobileDeviceRecord,
                MobileSessionRecord,
                MobileAuthIdentityRecord,
            ):
                await session.execute(delete(model).where(model.user_id == user_id))
            await session.execute(
                delete(MobileWeComBindingRecord).where(
                    or_(
                        MobileWeComBindingRecord.mobile_user_id == user_id,
                        MobileWeComBindingRecord.target_user_id == user_id,
                    )
                )
            )
            await session.execute(
                delete(UserMemoryEventRecord).where(UserMemoryEventRecord.user_id == user_id)
            )
            for user_data_model in (
                MemoryIndexOutboxRecord,
                MemoryBulkOperationRecord,
                MemoryHandoffRecord,
                UserMemoryFactRecord,
                WeightRecord,
                BodyFatRecord,
                MealRecord,
                ExerciseRecord,
                ImageAssetRecord,
                RoutineJobRecord,
                UserRoutinePreference,
                InteractionTraceRecord,
            ):
                await session.execute(
                    delete(user_data_model).where(user_data_model.user_id == user_id)
                )
            if turn_ids:
                await session.execute(
                    delete(PendingActionRecord).where(PendingActionRecord.turn_id.in_(turn_ids))
                )
                await session.execute(
                    delete(ToolExecutionRecord).where(ToolExecutionRecord.turn_id.in_(turn_ids))
                )
                await session.execute(
                    delete(AgentItemRecord).where(AgentItemRecord.turn_id.in_(turn_ids))
                )
                await session.execute(
                    delete(AgentTurnRecord).where(AgentTurnRecord.id.in_(turn_ids))
                )
            if thread_ids:
                await session.execute(
                    delete(AgentThreadRecord).where(AgentThreadRecord.id.in_(thread_ids))
                )
            if identities:
                route_match = or_(
                    *(
                        and_(
                            OutboundMessage.channel_id == identity.channel_id,
                            OutboundMessage.external_userid == identity.external_userid,
                        )
                        for identity in identities
                    )
                )
                await session.execute(delete(OutboundMessage).where(route_match))
                inbound_match = or_(
                    *(
                        and_(
                            InboundMessage.channel_id == identity.channel_id,
                            InboundMessage.external_userid == identity.external_userid,
                        )
                        for identity in identities
                    )
                )
                await session.execute(delete(InboundMessage).where(inbound_match))
                conversation_match = or_(
                    *(
                        and_(
                            WeComConversation.channel_id == identity.channel_id,
                            WeComConversation.external_userid == identity.external_userid,
                        )
                        for identity in identities
                    )
                )
                await session.execute(delete(WeComConversation).where(conversation_match))
                await session.execute(
                    delete(ChannelIdentity).where(ChannelIdentity.user_id == user_id)
                )
            await session.execute(delete(SlimGuardUser).where(SlimGuardUser.id == user_id))

    async def _move_mobile_account(
        self,
        session: AsyncSession,
        source_id: str,
        target_id: str,
    ) -> None:
        source = await session.get(SlimGuardUser, source_id)
        target = await session.get(SlimGuardUser, target_id)
        if source is None or target is None:
            raise MobilePlatformError("user_not_found", "User was not found")
        if target.nickname is None:
            target.nickname = source.nickname
        for model in (
            MobileAuthIdentityRecord,
            MobileSessionRecord,
            MobileDeviceRecord,
            MobileAgentRequestRecord,
        ):
            await session.execute(
                update(model).where(model.user_id == source_id).values(user_id=target_id)
            )
        source_routine = await session.get(UserRoutinePreference, source_id)
        target_routine = await session.get(UserRoutinePreference, target_id)
        if source_routine is not None:
            if target_routine is None:
                await session.execute(
                    update(UserRoutinePreference)
                    .where(UserRoutinePreference.user_id == source_id)
                    .values(user_id=target_id)
                )
            else:
                await session.delete(source_routine)

    @staticmethod
    def _device_view(row: MobileDeviceRecord) -> DeviceView:
        return DeviceView(
            id=row.id,
            installation_id=row.installation_id,
            platform=row.platform,
            push_provider=row.push_provider,
            app_version=row.app_version,
            timezone=row.timezone,
            locale=row.locale,
            active=row.revoked_at is None,
            last_seen_at=MobilePlatformService._aware(row.last_seen_at),
        )

    def _code_hash(self, code: str) -> str:
        return hmac.new(
            self._key,
            f"wecom-binding:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _binding_view(
        self,
        row: MobileWeComBindingRecord,
        *,
        code: str | None = None,
    ) -> WeComBindingView:
        return WeComBindingView(
            id=row.id,
            status=row.status,
            code=code,
            code_hint=row.code_hint,
            expires_at=self._aware(row.expires_at),
            claimed_at=(self._aware(row.claimed_at) if row.claimed_at is not None else None),
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)
