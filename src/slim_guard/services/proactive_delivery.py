from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import (
    ChannelIdentity,
    ProactiveMessageRecord,
    WeComConversation,
)
from slim_guard.db.session import Database


@dataclass(frozen=True, slots=True)
class ProactiveRoute:
    channel_id: str
    open_kfid: str
    external_userid: str
    last_customer_message_at: datetime


class ProactiveDeliveryStatus(StrEnum):
    PLANNED = "planned"
    SENDING = "sending"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProactiveDeliveryRef:
    job_id: str
    platform_msgid: str
    route: ProactiveRoute
    content: str
    source_turn_id: str
    status: ProactiveDeliveryStatus
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ProactiveEligibility:
    allowed: bool
    code: str
    route: ProactiveRoute | None = None


class ProactiveDeliveryRepository:
    """Owns user routing, proactive quota accounting, and idempotent sends."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def find_route(
        self,
        *,
        user_id: str,
        open_kfid: str,
    ) -> ProactiveRoute | None:
        async with self._database.session() as session:
            row = await session.execute(
                select(ChannelIdentity, WeComConversation)
                .join(
                    WeComConversation,
                    (WeComConversation.channel_id == ChannelIdentity.channel_id)
                    & (
                        WeComConversation.external_userid
                        == ChannelIdentity.external_userid
                    ),
                )
                .where(
                    ChannelIdentity.user_id == user_id,
                    WeComConversation.open_kfid == open_kfid,
                    WeComConversation.last_customer_message_at.is_not(None),
                )
                .order_by(WeComConversation.last_customer_message_at.desc())
                .limit(1)
            )
            found = row.one_or_none()
            if found is None:
                return None
            identity, conversation = found
            assert conversation.last_customer_message_at is not None
            return ProactiveRoute(
                channel_id=identity.channel_id,
                open_kfid=conversation.open_kfid,
                external_userid=identity.external_userid,
                last_customer_message_at=self._as_utc(
                    conversation.last_customer_message_at
                ),
            )

    async def count_since(self, *, route: ProactiveRoute, since: datetime) -> int:
        async with self._database.session() as session:
            count = await session.scalar(
                select(func.count(ProactiveMessageRecord.job_id)).where(
                    ProactiveMessageRecord.open_kfid == route.open_kfid,
                    ProactiveMessageRecord.external_userid == route.external_userid,
                    ProactiveMessageRecord.created_at >= since,
                    ProactiveMessageRecord.status.in_(
                        (
                            ProactiveDeliveryStatus.SENDING.value,
                            ProactiveDeliveryStatus.ACCEPTED.value,
                            ProactiveDeliveryStatus.UNKNOWN.value,
                        )
                    ),
                )
            )
            return int(count or 0)

    async def prepare(
        self,
        *,
        job_id: str,
        route: ProactiveRoute,
        content: str,
        source_turn_id: str,
    ) -> ProactiveDeliveryRef:
        normalized = content.strip()
        if not normalized:
            raise ValueError("Proactive message content cannot be empty")
        platform_msgid = hashlib.sha256(f"routine-job:{job_id}".encode()).hexdigest()[:32]
        row = ProactiveMessageRecord(
            job_id=job_id,
            platform_msgid=platform_msgid,
            channel_id=route.channel_id,
            open_kfid=route.open_kfid,
            external_userid=route.external_userid,
            last_customer_message_at=route.last_customer_message_at,
            content=normalized,
            source_turn_id=source_turn_id,
        )
        async with self._database.session() as session:
            session.add(row)
            try:
                await session.commit()
                return self._ref(row)
            except IntegrityError:
                await session.rollback()
                existing = await session.get(ProactiveMessageRecord, job_id)
                if existing is None:
                    raise
                if (
                    existing.channel_id,
                    existing.open_kfid,
                    existing.external_userid,
                    existing.content,
                    existing.source_turn_id,
                ) != (
                    route.channel_id,
                    route.open_kfid,
                    route.external_userid,
                    normalized,
                    source_turn_id,
                ):
                    raise ValueError("Proactive delivery idempotency collision") from None
                return self._ref(existing)

    async def get(self, job_id: str) -> ProactiveDeliveryRef | None:
        async with self._database.session() as session:
            row = await session.get(ProactiveMessageRecord, job_id)
            return self._ref(row) if row is not None else None

    async def claim(
        self,
        *,
        job_id: str,
        now: datetime,
        retry_after: timedelta,
    ) -> bool:
        stale_before = now - retry_after
        async with self._database.session() as session, session.begin():
            result = await session.execute(
                update(ProactiveMessageRecord)
                .where(
                    ProactiveMessageRecord.job_id == job_id,
                    or_(
                        ProactiveMessageRecord.status
                        == ProactiveDeliveryStatus.PLANNED.value,
                        (
                            ProactiveMessageRecord.status
                            == ProactiveDeliveryStatus.SENDING.value
                        )
                        & (ProactiveMessageRecord.attempt_started_at <= stale_before),
                    ),
                )
                .values(
                    status=ProactiveDeliveryStatus.SENDING.value,
                    attempt_count=ProactiveMessageRecord.attempt_count + 1,
                    attempt_started_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            return cast(CursorResult[Any], result).rowcount == 1

    async def complete(
        self,
        *,
        job_id: str,
        status: ProactiveDeliveryStatus,
        now: datetime,
        last_error: str | None = None,
    ) -> None:
        if status not in {
            ProactiveDeliveryStatus.ACCEPTED,
            ProactiveDeliveryStatus.UNKNOWN,
            ProactiveDeliveryStatus.FAILED,
        }:
            raise ValueError("Proactive delivery requires a terminal result")
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(ProactiveMessageRecord)
                .where(ProactiveMessageRecord.job_id == job_id)
                .values(
                    status=status.value,
                    last_error=last_error,
                    completed_at=now,
                )
            )

    @classmethod
    def _ref(
        cls,
        row: ProactiveMessageRecord,
    ) -> ProactiveDeliveryRef:
        return ProactiveDeliveryRef(
            job_id=row.job_id,
            platform_msgid=row.platform_msgid,
            route=ProactiveRoute(
                channel_id=row.channel_id,
                open_kfid=row.open_kfid,
                external_userid=row.external_userid,
                last_customer_message_at=cls._as_utc(row.last_customer_message_at),
            ),
            content=row.content,
            source_turn_id=row.source_turn_id,
            status=ProactiveDeliveryStatus(row.status),
            attempt_count=row.attempt_count,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ProactiveDeliveryPolicy:
    """Reserves scarce WeCom proactive capacity for only eligible routine jobs."""

    def __init__(
        self,
        *,
        repository: ProactiveDeliveryRepository,
        open_kfid: str,
        active_window: timedelta = timedelta(hours=48),
        max_messages_per_window: int = 3,
    ) -> None:
        if active_window <= timedelta(0):
            raise ValueError("Proactive active window must be positive")
        if not 1 <= max_messages_per_window <= 5:
            raise ValueError("Proactive message limit must be between 1 and 5")
        self._repository = repository
        self._open_kfid = open_kfid
        self._active_window = active_window
        self._max_messages = max_messages_per_window

    async def evaluate(self, *, user_id: str, now: datetime) -> ProactiveEligibility:
        if now.utcoffset() is None:
            raise ValueError("Proactive policy time must be timezone-aware")
        route = await self._repository.find_route(
            user_id=user_id,
            open_kfid=self._open_kfid,
        )
        if route is None:
            return ProactiveEligibility(False, "no_wecom_route")
        if now - route.last_customer_message_at > self._active_window:
            return ProactiveEligibility(False, "outside_customer_window", route)
        count = await self._repository.count_since(
            route=route,
            since=route.last_customer_message_at,
        )
        if count >= self._max_messages:
            return ProactiveEligibility(False, "proactive_quota_reserved", route)
        return ProactiveEligibility(True, "allowed", route)
