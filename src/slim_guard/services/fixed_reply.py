from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import replace

from slim_guard.db.repositories import ConversationRef, MessageRepository, OutboundPlan
from slim_guard.integrations.wecom_kf.client import WeComClientProtocol
from slim_guard.integrations.wecom_kf.errors import WeComAPIError, WeComTransportError
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState
from slim_guard.services.conversation_state import ConversationStateMachine
from slim_guard.services.reply_agent import ReplyAgentProtocol, ReplyRequest

logger = logging.getLogger(__name__)


class AgentReplySyncService:
    def __init__(
        self,
        *,
        client: WeComClientProtocol,
        repository: MessageRepository,
        channel_id: str,
        configured_open_kfid: str,
        reply_agent: ReplyAgentProtocol,
        fallback_reply_text: str,
        state_machine: ConversationStateMachine,
        reply_delivery_mode: str,
        profile_refresh_seconds: int = 86_400,
        media_max_bytes: int = 10_485_760,
    ) -> None:
        self.client = client
        self.repository = repository
        self.channel_id = channel_id
        self.configured_open_kfid = configured_open_kfid
        self.reply_agent = reply_agent
        self.fallback_reply_text = fallback_reply_text
        self.state_machine = state_machine
        self.reply_delivery_mode = reply_delivery_mode
        self.profile_refresh_seconds = profile_refresh_seconds
        self.media_max_bytes = media_max_bytes
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._allowed_message_types = frozenset({"text", "image"})

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
                    fallback_reply_text=self.fallback_reply_text,
                    allowed_message_types=self._allowed_message_types,
                    reply_delivery_mode=self.reply_delivery_mode,
                    profile_refresh_seconds=self.profile_refresh_seconds,
                )
                await self._sync_customer_profiles(stored_page.profile_external_userids)
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

    async def _sync_customer_profiles(self, external_userids: list[str]) -> None:
        if not external_userids:
            return
        try:
            batch = await self.client.get_customer_profiles(external_userids=external_userids)
        except (WeComAPIError, WeComTransportError) as exc:
            try:
                await self.repository.mark_user_profile_sync_failed(
                    channel_id=self.channel_id,
                    external_userids=external_userids,
                )
            except Exception:
                logger.exception(
                    "slim_guard_customer_profile_failure_state_save_failed",
                    extra={"customer_count": len(external_userids)},
                )
            logger.warning(
                "wecom_customer_profile_sync_failed",
                extra={
                    "customer_count": len(external_userids),
                    "result_code": getattr(exc, "errcode", None),
                },
            )
            return
        except Exception:
            logger.exception(
                "wecom_customer_profile_sync_unexpected_failure",
                extra={"customer_count": len(external_userids)},
            )
            return
        try:
            await self.repository.update_user_profiles(
                channel_id=self.channel_id,
                requested_external_userids=external_userids,
                profiles=batch.customer_list,
                invalid_external_userids=batch.invalid_external_userid,
            )
        except Exception:
            logger.exception(
                "slim_guard_customer_profile_save_failed",
                extra={"customer_count": len(external_userids)},
            )
            return
        logger.info(
            "wecom_customer_profiles_synced",
            extra={
                "customer_count": len(batch.customer_list),
                "invalid_customer_count": len(batch.invalid_external_userid),
            },
        )

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
            plan = await self._prepare_reply(plan, user_ref=user_ref)
            logger.info(
                "slim_guard_reply_pending_internal_review",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
            return

        plan = await self._prepare_reply(plan, user_ref=user_ref)
        await self._send(plan, user_ref=user_ref)

    async def _prepare_reply(self, plan: OutboundPlan, *, user_ref: str) -> OutboundPlan:
        if plan.input_text is None and plan.image_media_id is None:
            return plan
        try:
            user = await self.repository.get_user_context(
                channel_id=plan.channel_id,
                external_userid=plan.external_userid,
            )
            if user is None:
                raise RuntimeError("SlimGuard user mapping is missing")
            image_bytes: bytes | None = None
            image_mime_type: str | None = None
            if plan.image_media_id is not None:
                media = await self.client.download_media(
                    media_id=plan.image_media_id,
                    max_bytes=self.media_max_bytes,
                )
                image_bytes = media.content
                image_mime_type = media.content_type
            content = await self.reply_agent.generate_reply(
                ReplyRequest(
                    user_id=user.id,
                    nickname=user.nickname,
                    text=plan.input_text,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                )
            )
            if not content.strip():
                raise RuntimeError("Reply agent returned empty content")
        except Exception:
            content = self.fallback_reply_text
            logger.exception(
                "slim_guard_agent_reply_failed",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )
        await self.repository.update_outbound_content(plan.idempotency_key, content)
        return replace(plan, content=content, input_text=None, image_media_id=None)

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
                "wecom_agent_reply_accepted",
                extra={"message_id": plan.platform_msgid, "user_ref": user_ref},
            )


# Temporary compatibility alias for callers that still import the Phase 1 name.
FixedReplySyncService = AgentReplySyncService
