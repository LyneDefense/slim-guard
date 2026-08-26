from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import InboundMessage, OutboundMessage, WeComSyncState
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
    ) -> list[OutboundPlan]:
        message_ids = [message.msgid for message in messages]
        async with self.database.session() as session, session.begin():
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
                        status="planned",
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
            return plans

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

    async def count_outbound(self) -> int:
        async with self.database.session() as session:
            rows = await session.scalars(select(OutboundMessage.idempotency_key))
            return len(list(rows))
