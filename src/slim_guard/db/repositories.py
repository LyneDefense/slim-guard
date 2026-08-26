from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import (
    InboundMessage,
    OutboundMessage,
    WeComConversation,
    WeComSyncState,
)
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.schemas import SyncMessage

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
    requires_review: bool = False


@dataclass(frozen=True, slots=True)
class ConversationRef:
    channel_id: str
    open_kfid: str
    external_userid: str


@dataclass(frozen=True, slots=True)
class StoredPage:
    plans: list[OutboundPlan]
    customer_conversations: list[ConversationRef]


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

    async def store_page(
        self,
        *,
        channel_id: str,
        open_kfid: str,
        messages: list[SyncMessage],
        next_cursor: str | None,
        fixed_reply_text: str,
        allowed_message_types: frozenset[str],
        reply_delivery_mode: str,
    ) -> StoredPage:
        message_ids = [message.msgid for message in messages]
        async with self.database.session() as session, session.begin():
            conversation_cache: dict[tuple[str, str, str], WeComConversation] = {}
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
                    elif message.origin == 4 and (
                        conversation.last_servicer_message_at is None
                        or sent_at > self._as_utc(conversation.last_servicer_message_at)
                    ):
                        conversation.last_servicer_message_at = sent_at

                if not self._should_reply(message, allowed_message_types):
                    continue
                assert message.external_userid is not None
                idempotency_key = sha256(
                    f"{channel_id}:{message.msgid}:fixed-reply-v1".encode()
                ).hexdigest()
                platform_msgid = idempotency_key[:32]
                plan = OutboundPlan(
                    idempotency_key=idempotency_key,
                    platform_msgid=platform_msgid,
                    channel_id=channel_id,
                    inbound_msgid=message.msgid,
                    open_kfid=open_kfid,
                    external_userid=message.external_userid,
                    content=fixed_reply_text,
                    requires_review=reply_delivery_mode == "internal_review",
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
            )

    @staticmethod
    def _should_reply(
        message: SyncMessage,
        allowed_message_types: frozenset[str],
    ) -> bool:
        return (
            message.origin == CUSTOMER_ORIGIN
            and message.msgtype in allowed_message_types
            and bool(message.external_userid)
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    async def claim(self, plan: OutboundPlan) -> bool:
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            result = await session.execute(
                update(OutboundMessage)
                .where(
                    OutboundMessage.idempotency_key == plan.idempotency_key,
                    OutboundMessage.status == "planned",
                )
                .values(status="sending", attempt_started_at=now)
            )
            cursor_result = cast(CursorResult[Any], result)
            return cursor_result.rowcount > 0

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

    async def count_outbound(self) -> int:
        async with self.database.session() as session:
            rows = await session.scalars(select(OutboundMessage.idempotency_key))
            return len(list(rows))
