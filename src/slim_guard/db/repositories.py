from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import (
    ChannelIdentity,
    InboundMessage,
    OutboundMessage,
    SlimGuardUser,
    WeComConversation,
    WeComSyncState,
    new_uuid,
)
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.schemas import CustomerProfile, SyncMessage

CUSTOMER_ORIGIN = 3


@dataclass(frozen=True, slots=True)
class OutboundPlan:
    idempotency_key: str
    platform_msgid: str
    channel_id: str
    inbound_msgid: str
    open_kfid: str
    external_userid: str
    content: str
    occurred_at: datetime | None = None
    requires_review: bool = False
    input_text: str | None = None
    image_media_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationRef:
    channel_id: str
    open_kfid: str
    external_userid: str


@dataclass(frozen=True, slots=True)
class StoredPage:
    plans: list[OutboundPlan]
    customer_conversations: list[ConversationRef]
    profile_external_userids: list[str]


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: str
    channel_id: str
    external_userid: str
    nickname: str | None
    avatar_url: str | None
    gender: int | None
    unionid: str | None
    profile_status: str
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class UserContext:
    id: str
    nickname: str | None


class MessageRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_cursor(self, channel_id: str, open_kfid: str) -> str | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(WeComSyncState.cursor).where(
                    WeComSyncState.channel_id == channel_id,
                    WeComSyncState.open_kfid == open_kfid,
                )
            )

    async def backfill_users_from_messages(self) -> int:
        """Create user identities for customer messages stored before the users table existed."""

        async with self.database.session() as session, session.begin():
            existing_rows = await session.execute(
                select(ChannelIdentity.channel_id, ChannelIdentity.external_userid)
            )
            existing_keys = set(existing_rows.tuples())
            history_rows = await session.execute(
                select(
                    InboundMessage.channel_id,
                    InboundMessage.external_userid,
                    func.min(InboundMessage.send_time),
                    func.max(InboundMessage.send_time),
                )
                .where(
                    InboundMessage.origin == CUSTOMER_ORIGIN,
                    InboundMessage.external_userid.is_not(None),
                )
                .group_by(InboundMessage.channel_id, InboundMessage.external_userid)
            )
            created = 0
            for channel_id, external_userid, first_seen_at, last_seen_at in history_rows:
                if external_userid is None or (channel_id, external_userid) in existing_keys:
                    continue
                user_id = new_uuid()
                session.add(
                    SlimGuardUser(
                        id=user_id,
                        first_seen_at=first_seen_at,
                        last_seen_at=last_seen_at,
                    )
                )
                session.add(
                    ChannelIdentity(
                        channel_id=channel_id,
                        external_userid=external_userid,
                        user_id=user_id,
                    )
                )
                created += 1
            return created

    async def store_page(
        self,
        *,
        channel_id: str,
        open_kfid: str,
        messages: list[SyncMessage],
        next_cursor: str | None,
        fallback_reply_text: str,
        allowed_message_types: frozenset[str],
        reply_delivery_mode: str,
        profile_refresh_seconds: int,
    ) -> StoredPage:
        message_ids = [message.msgid for message in messages]
        profile_refresh_cutoff = datetime.now(UTC) - timedelta(seconds=profile_refresh_seconds)
        async with self.database.session() as session, session.begin():
            conversation_cache: dict[tuple[str, str, str], WeComConversation] = {}
            identity_cache: dict[tuple[str, str], ChannelIdentity] = {}
            user_cache: dict[str, SlimGuardUser] = {}
            existing_ids: set[str] = set()
            if message_ids:
                result = await session.scalars(
                    select(InboundMessage.msgid).where(
                        InboundMessage.channel_id == channel_id,
                        InboundMessage.msgid.in_(message_ids),
                    )
                )
                existing_ids = set(result)

            plans: list[OutboundPlan] = []
            customer_conversations: dict[tuple[str, str, str], ConversationRef] = {}
            profile_external_userids: dict[str, None] = {}
            seen_in_page: set[str] = set()
            for message in messages:
                if message.msgid in existing_ids or message.msgid in seen_in_page:
                    continue
                seen_in_page.add(message.msgid)
                session.add(
                    InboundMessage(
                        channel_id=channel_id,
                        msgid=message.msgid,
                        open_kfid=open_kfid,
                        external_userid=message.external_userid,
                        msgtype=message.msgtype,
                        origin=message.origin,
                        send_time=datetime.fromtimestamp(message.send_time, tz=UTC),
                    )
                )

                if message.external_userid:
                    conversation_key = (channel_id, open_kfid, message.external_userid)
                    conversation = conversation_cache.get(conversation_key)
                    if conversation is None:
                        conversation = await session.get(
                            WeComConversation,
                            {
                                "channel_id": channel_id,
                                "open_kfid": open_kfid,
                                "external_userid": message.external_userid,
                            },
                        )
                        if conversation is None:
                            conversation = WeComConversation(
                                channel_id=channel_id,
                                open_kfid=open_kfid,
                                external_userid=message.external_userid,
                            )
                            session.add(conversation)
                        conversation_cache[conversation_key] = conversation
                    sent_at = datetime.fromtimestamp(message.send_time, tz=UTC)
                    if message.origin == CUSTOMER_ORIGIN:
                        if conversation.last_customer_message_at is None or sent_at > self._as_utc(
                            conversation.last_customer_message_at
                        ):
                            conversation.last_customer_message_at = sent_at
                            conversation.human_timeout_handled_at = None
                        customer_conversations[conversation_key] = ConversationRef(
                            channel_id=channel_id,
                            open_kfid=open_kfid,
                            external_userid=message.external_userid,
                        )
                        identity_key = (channel_id, message.external_userid)
                        identity = identity_cache.get(identity_key)
                        if identity is None:
                            identity = await session.get(
                                ChannelIdentity,
                                {
                                    "channel_id": channel_id,
                                    "external_userid": message.external_userid,
                                },
                            )
                            if identity is None:
                                user_id = new_uuid()
                                user = SlimGuardUser(
                                    id=user_id,
                                    first_seen_at=sent_at,
                                    last_seen_at=sent_at,
                                )
                                identity = ChannelIdentity(
                                    channel_id=channel_id,
                                    external_userid=message.external_userid,
                                    user_id=user_id,
                                )
                                session.add(user)
                                session.add(identity)
                                user_cache[user_id] = user
                            identity_cache[identity_key] = identity
                        cached_user = user_cache.get(identity.user_id)
                        if cached_user is None:
                            cached_user = await session.get(SlimGuardUser, identity.user_id)
                            assert cached_user is not None
                            user_cache[cached_user.id] = cached_user
                        user = cached_user
                        if sent_at < self._as_utc(user.first_seen_at):
                            user.first_seen_at = sent_at
                        if sent_at > self._as_utc(user.last_seen_at):
                            user.last_seen_at = sent_at
                        if (
                            identity.profile_synced_at is None
                            or self._as_utc(identity.profile_synced_at) <= profile_refresh_cutoff
                        ):
                            profile_external_userids[message.external_userid] = None
                    elif message.origin == 4 and (
                        conversation.last_servicer_message_at is None
                        or sent_at > self._as_utc(conversation.last_servicer_message_at)
                    ):
                        conversation.last_servicer_message_at = sent_at

                input_text = self._message_text(message)
                image_media_id = self._image_media_id(message)
                if not self._should_reply(
                    message,
                    allowed_message_types,
                    input_text=input_text,
                    image_media_id=image_media_id,
                ):
                    continue
                assert message.external_userid is not None
                idempotency_key = sha256(
                    f"{channel_id}:{message.msgid}:agent-reply-v1".encode()
                ).hexdigest()
                platform_msgid = idempotency_key[:32]
                plan = OutboundPlan(
                    idempotency_key=idempotency_key,
                    platform_msgid=platform_msgid,
                    channel_id=channel_id,
                    inbound_msgid=message.msgid,
                    open_kfid=open_kfid,
                    external_userid=message.external_userid,
                    content=fallback_reply_text,
                    occurred_at=datetime.fromtimestamp(message.send_time, tz=UTC),
                    requires_review=reply_delivery_mode == "internal_review",
                    input_text=input_text,
                    image_media_id=image_media_id,
                )
                session.add(
                    OutboundMessage(
                        idempotency_key=plan.idempotency_key,
                        platform_msgid=plan.platform_msgid,
                        channel_id=plan.channel_id,
                        inbound_msgid=plan.inbound_msgid,
                        open_kfid=plan.open_kfid,
                        external_userid=plan.external_userid,
                        content=plan.content,
                        status=("pending_review" if plan.requires_review else "planned"),
                    )
                )
                plans.append(plan)

            state = await session.get(
                WeComSyncState,
                {"channel_id": channel_id, "open_kfid": open_kfid},
            )
            if state is None:
                state = WeComSyncState(channel_id=channel_id, open_kfid=open_kfid)
                session.add(state)
            if next_cursor is not None:
                state.cursor = next_cursor
            state.last_success_at = datetime.now(UTC)
            return StoredPage(
                plans=plans,
                customer_conversations=list(customer_conversations.values()),
                profile_external_userids=list(profile_external_userids),
            )

    @staticmethod
    def _should_reply(
        message: SyncMessage,
        allowed_message_types: frozenset[str],
        *,
        input_text: str | None,
        image_media_id: str | None,
    ) -> bool:
        return (
            message.origin == CUSTOMER_ORIGIN
            and message.msgtype in allowed_message_types
            and bool(message.external_userid)
            and bool(input_text or image_media_id)
        )

    @staticmethod
    def _message_text(message: SyncMessage) -> str | None:
        if message.msgtype != "text" or not message.text:
            return None
        content = message.text.get("content")
        if not isinstance(content, str):
            return None
        stripped = content.strip()
        return stripped or None

    @staticmethod
    def _image_media_id(message: SyncMessage) -> str | None:
        if message.msgtype != "image" or not message.image:
            return None
        media_id = message.image.get("media_id")
        if not isinstance(media_id, str):
            return None
        stripped = media_id.strip()
        return stripped or None

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    async def claim(
        self,
        plan: OutboundPlan,
        *,
        now: datetime | None = None,
        retry_after: timedelta = timedelta(minutes=2),
    ) -> bool:
        reference_time = now or datetime.now(UTC)
        stale_before = reference_time - retry_after
        async with self.database.session() as session, session.begin():
            result = await session.execute(
                update(OutboundMessage)
                .where(
                    OutboundMessage.idempotency_key == plan.idempotency_key,
                    or_(
                        OutboundMessage.status == "planned",
                        (
                            (OutboundMessage.status == "sending")
                            & (OutboundMessage.attempt_started_at <= stale_before)
                        ),
                    ),
                )
                .values(status="sending", attempt_started_at=reference_time)
                .execution_options(synchronize_session=False)
            )
            cursor_result = cast(CursorResult[Any], result)
            return cursor_result.rowcount > 0

    async def list_recoverable_outbound(
        self,
        *,
        stale_before: datetime,
        limit: int = 100,
    ) -> list[OutboundPlan]:
        if stale_before.utcoffset() is None:
            raise ValueError("Outbound recovery cutoff must be timezone-aware")
        async with self.database.session() as session:
            rows = await session.scalars(
                select(OutboundMessage)
                .where(
                    or_(
                        (OutboundMessage.status == "planned")
                        & (OutboundMessage.created_at <= stale_before),
                        (
                            (OutboundMessage.status == "sending")
                            & (OutboundMessage.attempt_started_at <= stale_before)
                        ),
                    )
                )
                .order_by(OutboundMessage.created_at, OutboundMessage.idempotency_key)
                .limit(limit)
            )
            return [
                OutboundPlan(
                    idempotency_key=row.idempotency_key,
                    platform_msgid=row.platform_msgid,
                    channel_id=row.channel_id,
                    inbound_msgid=row.inbound_msgid,
                    open_kfid=row.open_kfid,
                    external_userid=row.external_userid,
                    content=row.content,
                )
                for row in rows
            ]

    async def complete(
        self,
        idempotency_key: str,
        *,
        status: str,
        last_error: str | None = None,
    ) -> None:
        async with self.database.session() as session, session.begin():
            await session.execute(
                update(OutboundMessage)
                .where(OutboundMessage.idempotency_key == idempotency_key)
                .values(
                    status=status,
                    last_error=last_error,
                    completed_at=datetime.now(UTC),
                )
            )

    async def update_outbound_content(self, idempotency_key: str, content: str) -> None:
        async with self.database.session() as session, session.begin():
            await session.execute(
                update(OutboundMessage)
                .where(OutboundMessage.idempotency_key == idempotency_key)
                .values(content=content)
            )

    async def record_service_state(
        self,
        *,
        channel_id: str,
        open_kfid: str,
        external_userid: str,
        service_state: int,
        servicer_userid: str | None = None,
        changed: bool = False,
        human_timeout_handled: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            conversation = await session.get(
                WeComConversation,
                {
                    "channel_id": channel_id,
                    "open_kfid": open_kfid,
                    "external_userid": external_userid,
                },
            )
            if conversation is None:
                conversation = WeComConversation(
                    channel_id=channel_id,
                    open_kfid=open_kfid,
                    external_userid=external_userid,
                )
                session.add(conversation)
            state_changed = conversation.service_state != service_state
            conversation.service_state = service_state
            conversation.servicer_userid = servicer_userid
            conversation.last_state_checked_at = now
            if changed or state_changed:
                conversation.last_state_changed_at = now
            if human_timeout_handled:
                conversation.human_timeout_handled_at = now

    async def update_user_profiles(
        self,
        *,
        channel_id: str,
        requested_external_userids: list[str],
        profiles: list[CustomerProfile],
        invalid_external_userids: list[str],
    ) -> None:
        if not requested_external_userids:
            return
        profile_by_id = {profile.external_userid: profile for profile in profiles}
        invalid_ids = set(invalid_external_userids)
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            rows = await session.execute(
                select(ChannelIdentity, SlimGuardUser)
                .join(SlimGuardUser, SlimGuardUser.id == ChannelIdentity.user_id)
                .where(
                    ChannelIdentity.channel_id == channel_id,
                    ChannelIdentity.external_userid.in_(requested_external_userids),
                )
            )
            for identity, user in rows:
                profile = profile_by_id.get(identity.external_userid)
                if profile is not None:
                    user.nickname = profile.nickname
                    user.avatar_url = profile.avatar
                    user.gender = profile.gender
                    identity.unionid = profile.unionid
                    identity.profile_status = "synced"
                elif identity.external_userid in invalid_ids:
                    identity.profile_status = "invalid"
                else:
                    identity.profile_status = "missing"
                identity.profile_synced_at = now

    async def mark_user_profile_sync_failed(
        self, *, channel_id: str, external_userids: list[str]
    ) -> None:
        if not external_userids:
            return
        async with self.database.session() as session, session.begin():
            await session.execute(
                update(ChannelIdentity)
                .where(
                    ChannelIdentity.channel_id == channel_id,
                    ChannelIdentity.external_userid.in_(external_userids),
                )
                .values(profile_status="error", profile_synced_at=datetime.now(UTC))
            )

    async def list_timed_out_human_conversations(
        self, *, cutoff: datetime, limit: int = 100
    ) -> list[ConversationRef]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(WeComConversation)
                .where(
                    WeComConversation.service_state == 3,
                    WeComConversation.last_customer_message_at.is_not(None),
                    WeComConversation.last_customer_message_at <= cutoff,
                    or_(
                        WeComConversation.last_servicer_message_at.is_(None),
                        WeComConversation.last_servicer_message_at
                        < WeComConversation.last_customer_message_at,
                    ),
                    WeComConversation.human_timeout_handled_at.is_(None),
                )
                .order_by(WeComConversation.last_customer_message_at)
                .limit(limit)
            )
            return [
                ConversationRef(
                    channel_id=row.channel_id,
                    open_kfid=row.open_kfid,
                    external_userid=row.external_userid,
                )
                for row in rows
            ]

    async def approve_review(self, idempotency_key: str) -> OutboundPlan | None:
        """Approve an internal review item without entering WeCom human state 3."""

        async with self.database.session() as session, session.begin():
            result = await session.execute(
                update(OutboundMessage)
                .where(
                    OutboundMessage.idempotency_key == idempotency_key,
                    OutboundMessage.status == "pending_review",
                )
                .values(status="planned")
            )
            if cast(CursorResult[Any], result).rowcount == 0:
                return None
            row = await session.get(OutboundMessage, idempotency_key)
            assert row is not None
            return OutboundPlan(
                idempotency_key=row.idempotency_key,
                platform_msgid=row.platform_msgid,
                channel_id=row.channel_id,
                inbound_msgid=row.inbound_msgid,
                open_kfid=row.open_kfid,
                external_userid=row.external_userid,
                content=row.content,
            )

    async def list_pending_reviews(self, *, limit: int = 100) -> list[OutboundPlan]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(OutboundMessage)
                .where(OutboundMessage.status == "pending_review")
                .order_by(OutboundMessage.created_at)
                .limit(limit)
            )
            return [
                OutboundPlan(
                    idempotency_key=row.idempotency_key,
                    platform_msgid=row.platform_msgid,
                    channel_id=row.channel_id,
                    inbound_msgid=row.inbound_msgid,
                    open_kfid=row.open_kfid,
                    external_userid=row.external_userid,
                    content=row.content,
                    requires_review=True,
                )
                for row in rows
            ]

    async def list_users(self, *, limit: int = 100) -> list[UserSummary]:
        async with self.database.session() as session:
            rows = await session.execute(
                select(SlimGuardUser, ChannelIdentity)
                .join(ChannelIdentity, ChannelIdentity.user_id == SlimGuardUser.id)
                .order_by(SlimGuardUser.created_at)
                .limit(limit)
            )
            return [
                UserSummary(
                    id=user.id,
                    channel_id=identity.channel_id,
                    external_userid=identity.external_userid,
                    nickname=user.nickname,
                    avatar_url=user.avatar_url,
                    gender=user.gender,
                    unionid=identity.unionid,
                    profile_status=identity.profile_status,
                    first_seen_at=self._as_utc(user.first_seen_at),
                    last_seen_at=self._as_utc(user.last_seen_at),
                )
                for user, identity in rows
            ]

    async def get_user_context(
        self, *, channel_id: str, external_userid: str
    ) -> UserContext | None:
        async with self.database.session() as session:
            row = await session.execute(
                select(SlimGuardUser.id, SlimGuardUser.nickname)
                .join(ChannelIdentity, ChannelIdentity.user_id == SlimGuardUser.id)
                .where(
                    ChannelIdentity.channel_id == channel_id,
                    ChannelIdentity.external_userid == external_userid,
                )
            )
            result = row.one_or_none()
            if result is None:
                return None
            return UserContext(id=result.id, nickname=result.nickname)

    async def count_outbound(self) -> int:
        async with self.database.session() as session:
            rows = await session.scalars(select(OutboundMessage.idempotency_key))
            return len(list(rows))
