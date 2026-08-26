from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from slim_guard.db.repositories import ConversationRef, MessageRepository
from slim_guard.integrations.wecom_kf.client import WeComClientProtocol
from slim_guard.integrations.wecom_kf.errors import WeComAPIError, WeComTransportError
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState

logger = logging.getLogger(__name__)


class ConversationStateMachine:
    """Keeps WeCom sessions owned by the agent and recovers stale human sessions."""

    def __init__(
        self,
        *,
        client: WeComClientProtocol,
        repository: MessageRepository,
        human_idle_timeout_seconds: int,
        watchdog_interval_seconds: int,
        human_timeout_message: str,
    ) -> None:
        self.client = client
        self.repository = repository
        self.human_idle_timeout = timedelta(seconds=human_idle_timeout_seconds)
        self.watchdog_interval_seconds = watchdog_interval_seconds
        self.human_timeout_message = human_timeout_message

    async def ensure_agent_control(self, conversation: ConversationRef) -> WeComServiceState | None:
        snapshot = await self.client.get_service_state(
            external_userid=conversation.external_userid,
            open_kfid=conversation.open_kfid,
        )
        try:
            state = WeComServiceState(snapshot.service_state)
        except ValueError:
            await self.repository.record_service_state(
                channel_id=conversation.channel_id,
                open_kfid=conversation.open_kfid,
                external_userid=conversation.external_userid,
                service_state=snapshot.service_state,
                servicer_userid=snapshot.servicer_userid,
            )
            logger.warning(
                "wecom_unknown_service_state",
                extra={
                    "user_ref": self._user_ref(conversation.external_userid),
                    "service_state": snapshot.service_state,
                },
            )
            return None

        if state is WeComServiceState.UNPROCESSED:
            await self.client.transition_service_state(
                external_userid=conversation.external_userid,
                open_kfid=conversation.open_kfid,
                service_state=WeComServiceState.SMART_ASSISTANT,
            )
            await self.repository.record_service_state(
                channel_id=conversation.channel_id,
                open_kfid=conversation.open_kfid,
                external_userid=conversation.external_userid,
                service_state=int(WeComServiceState.SMART_ASSISTANT),
                changed=True,
            )
            logger.info(
                "wecom_session_claimed_by_agent",
                extra={"user_ref": self._user_ref(conversation.external_userid)},
            )
            return WeComServiceState.SMART_ASSISTANT

        await self.repository.record_service_state(
            channel_id=conversation.channel_id,
            open_kfid=conversation.open_kfid,
            external_userid=conversation.external_userid,
            service_state=int(state),
            servicer_userid=snapshot.servicer_userid,
        )
        return state

    async def run_watchdog(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.handle_human_timeouts_once()
            except Exception:
                logger.exception("wecom_human_timeout_watchdog_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.watchdog_interval_seconds)
            except TimeoutError:
                pass

    async def handle_human_timeouts_once(self, *, now: datetime | None = None) -> int:
        reference_time = now or datetime.now(UTC)
        conversations = await self.repository.list_timed_out_human_conversations(
            cutoff=reference_time - self.human_idle_timeout
        )
        ended_count = 0
        for conversation in conversations:
            try:
                ended = await self._end_if_still_human(conversation)
            except (WeComAPIError, WeComTransportError) as exc:
                logger.warning(
                    "wecom_human_timeout_recovery_failed",
                    extra={
                        "user_ref": self._user_ref(conversation.external_userid),
                        "result_code": getattr(exc, "errcode", None),
                    },
                )
                continue
            ended_count += int(ended)
        return ended_count

    async def _end_if_still_human(self, conversation: ConversationRef) -> bool:
        snapshot = await self.client.get_service_state(
            external_userid=conversation.external_userid,
            open_kfid=conversation.open_kfid,
        )
        if snapshot.service_state != int(WeComServiceState.HUMAN):
            await self.repository.record_service_state(
                channel_id=conversation.channel_id,
                open_kfid=conversation.open_kfid,
                external_userid=conversation.external_userid,
                service_state=snapshot.service_state,
                servicer_userid=snapshot.servicer_userid,
            )
            return False

        transition = await self.client.transition_service_state(
            external_userid=conversation.external_userid,
            open_kfid=conversation.open_kfid,
            service_state=WeComServiceState.ENDED,
        )
        await self.repository.record_service_state(
            channel_id=conversation.channel_id,
            open_kfid=conversation.open_kfid,
            external_userid=conversation.external_userid,
            service_state=int(WeComServiceState.ENDED),
            changed=True,
            human_timeout_handled=True,
        )
        if transition.msg_code:
            try:
                await self.client.send_event_text(
                    code=transition.msg_code,
                    content=self.human_timeout_message,
                    msgid=hashlib.sha256(
                        f"human-timeout:{transition.msg_code}".encode()
                    ).hexdigest()[:32],
                )
            except (WeComAPIError, WeComTransportError) as exc:
                logger.warning(
                    "wecom_human_timeout_notice_failed",
                    extra={
                        "user_ref": self._user_ref(conversation.external_userid),
                        "result_code": getattr(exc, "errcode", None),
                    },
                )
        logger.info(
            "wecom_human_session_ended_after_timeout",
            extra={"user_ref": self._user_ref(conversation.external_userid)},
        )
        return True

    @staticmethod
    def _user_ref(external_userid: str) -> str:
        return hashlib.sha256(external_userid.encode()).hexdigest()[:12]
