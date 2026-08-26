from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict

from slim_guard.db.repositories import ConversationRef, MessageRepository, OutboundPlan
from slim_guard.integrations.wecom_kf.client import WeComClientProtocol
from slim_guard.integrations.wecom_kf.errors import WeComAPIError, WeComTransportError
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.conversation_state import ConversationStateMachine

logger = logging.getLogger(__name__)


class FixedReplySyncService:
    def __init__(
        self,
        *,
        client: WeComClientProtocol,
        repository: MessageRepository,
        channel_id: str,
        configured_open_kfid: str,
        fixed_reply_text: str,
        state_machine: ConversationStateMachine,
        reply_delivery_mode: str,
    ) -> None:
        self.client = client
        self.repository = repository
        self.channel_id = channel_id
        self.configured_open_kfid = configured_open_kfid
        self.fixed_reply_text = fixed_reply_text
        self.state_machine = state_machine
        self.reply_delivery_mode = reply_delivery_mode
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._allowed_message_types = frozenset({"text"})

    async def handle_callback(self, callback_token: str, open_kfid: str) -> None:
        try:
            await self.sync_and_reply(callback_token=callback_token, open_kfid=open_kfid)
        except Exception:
            logger.exception(
                "wecom_sync_failed",
                extra={"channel_id": self.channel_id, "open_kfid": open_kfid},
            )

    async def sync_and_reply(self, *, callback_token: str, open_kfid: str) -> None:
        if open_kfid != self.configured_open_kfid:
            logger.warning(
                "ignored_unconfigured_kf_account",
                extra={"channel_id": self.channel_id, "open_kfid": open_kfid},
            )
            return

        lock = self._locks[(self.channel_id, open_kfid)]
        async with lock:
            cursor = await self.repository.get_cursor(self.channel_id, open_kfid)
            page_count = 0
            while True:
                page_count += 1
                if page_count > 100:
                    raise RuntimeError("sync_msg exceeded the 100-page safety limit")
                page = await self.client.sync_messages(
                    callback_token=callback_token,
                    open_kfid=open_kfid,
                    cursor=cursor,
                )
                stored_page = await self.repository.store_page(
                    channel_id=self.channel_id,
                    open_kfid=open_kfid,
                    messages=page.msg_list,
                    next_cursor=page.next_cursor,
                    fixed_reply_text=self.fixed_reply_text,
                    allowed_message_types=self._allowed_message_types,
                    reply_delivery_mode=self.reply_delivery_mode,
                )
                planned_users = {plan.external_userid for plan in stored_page.plans}
                for conversation in stored_page.customer_conversations:
                    if conversation.external_userid not in planned_users:
                        await self._claim_conversation_without_reply(conversation)
                for plan in stored_page.plans:
                    await self._dispatch(plan)

                logger.info(
                    "wecom_page_synced",
                    extra={
                        "channel_id": self.channel_id,
                        "open_kfid": open_kfid,
                        "page": page_count,
                        "message_count": len(page.msg_list),
                        "new_reply_count": len(stored_page.plans),
                        "has_more": page.has_more,
                    },
                )
                if not page.has_more:
                    return
                if not page.next_cursor or page.next_cursor == cursor:
                    raise RuntimeError("sync_msg returned has_more without a new cursor")
                cursor = page.next_cursor

    async def dispatch_approved(self, plan: OutboundPlan) -> None:
        """Future review UIs can approve a draft and dispatch it through this method."""

        await self._dispatch(plan)

    async def _claim_conversation_without_reply(self, conversation: ConversationRef) -> None:
        try:
            state = await self.state_machine.ensure_agent_control(conversation)
        except (WeComAPIError, WeComTransportError) as exc:
            logger.warning(
                "wecom_service_state_failed_without_reply",
                extra={
                    "user_ref": hashlib.sha256(conversation.external_userid.encode()).hexdigest()[
                        :12
                    ],
                    "result_code": getattr(exc, "errcode", None),
                },
            )
            return
        if state is not WeComServiceState.SMART_ASSISTANT:
            logger.warning(
                "wecom_message_deferred_by_service_state",
                extra={
                    "user_ref": hashlib.sha256(conversation.external_userid.encode()).hexdigest()[
                        :12
                    ],
                    "service_state": int(state) if state is not None else None,
                },
            )

    async def _dispatch(self, plan: OutboundPlan) -> None:
        user_ref = hashlib.sha256(plan.external_userid.encode()).hexdigest()[:12]
        try:
            state = await self.state_machine.ensure_agent_control(
                ConversationRef(
                    channel_id=plan.channel_id,
                    open_kfid=plan.open_kfid,
                    external_userid=plan.external_userid,
                )
            )
        except WeComTransportError as exc:
            await self.repository.complete(
                plan.idempotency_key,
                status="unknown",
                last_error=type(exc).__name__,
            )
            logger.warning(
                "wecom_service_state_result_unknown",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
            return
        except WeComAPIError as exc:
            await self.repository.complete(
                plan.idempotency_key,
                status="failed",
                last_error=f"{exc.errcode}:{exc.errmsg}",
            )
            logger.warning(
                "wecom_service_state_failed",
                extra={
                    "message_id": plan.platform_msgid,
                    "user_ref": user_ref,
                    "result_code": exc.errcode,
                },
            )
            return

        if state is not WeComServiceState.SMART_ASSISTANT:
            await self.repository.complete(
                plan.idempotency_key,
                status="deferred_external_session",
                last_error=f"service_state:{int(state) if state is not None else 'unknown'}",
            )
            logger.warning(
                "wecom_reply_deferred_by_service_state",
                extra={
                    "message_id": plan.platform_msgid,
                    "user_ref": user_ref,
                    "service_state": int(state) if state is not None else None,
                },
            )
            return

        if plan.requires_review:
            logger.info(
                "slim_guard_reply_pending_internal_review",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
            return

        await self._send(plan, user_ref=user_ref)

    async def _send(self, plan: OutboundPlan, *, user_ref: str) -> None:
        if not await self.repository.claim(plan):
            return
        try:
            await self.client.send_text(
                external_userid=plan.external_userid,
                open_kfid=plan.open_kfid,
                content=plan.content,
                msgid=plan.platform_msgid,
            )
        except WeComTransportError as exc:
            await self.repository.complete(
                plan.idempotency_key,
                status="unknown",
                last_error=type(exc).__name__,
            )
            logger.warning(
                "wecom_send_result_unknown",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
        except WeComAPIError as exc:
            await self.repository.complete(
                plan.idempotency_key,
                status="failed",
                last_error=f"{exc.errcode}:{exc.errmsg}",
            )
            logger.warning(
                "wecom_send_failed",
                extra={
                    "message_id": plan.platform_msgid,
                    "user_ref": user_ref,
                    "result_code": exc.errcode,
                },
            )
        else:
            await self.repository.complete(plan.idempotency_key, status="accepted")
            logger.info(
                "wecom_fixed_reply_accepted",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
