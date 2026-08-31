from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from slim_guard.agent.runtime import AgentRuntimeProtocol, AgentRuntimeRequest
from slim_guard.harness.termination import HarnessTermination
from slim_guard.services.reply_agent import ReplyRequest
from slim_guard.tools.contracts import ToolExecutionMode


class HarnessReplyError(RuntimeError):
    pass


class HarnessReplyAgent:
    """Adapts the WeCom reply contract to the channel-independent Agent Runtime."""

    def __init__(
        self,
        *,
        runtime: AgentRuntimeProtocol,
        max_reply_chars: int,
        turn_timeout_seconds: float = 120,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_reply_chars < 1:
            raise ValueError("Harness reply length must be positive")
        if turn_timeout_seconds <= 0:
            raise ValueError("Harness Turn timeout must be positive")
        self._runtime = runtime
        self._max_reply_chars = max_reply_chars
        self._turn_timeout = timedelta(seconds=turn_timeout_seconds)
        self._clock = clock or self._utc_now

    async def generate_reply(self, request: ReplyRequest) -> str:
        if request.text is None and request.image_bytes is None:
            raise HarnessReplyError("Harness reply requires text or an image")
        now = self._clock()
        if now.utcoffset() is None:
            raise HarnessReplyError("Harness reply clock must be timezone-aware")
        result = await self._runtime.run_user_message(
            AgentRuntimeRequest(
                user_id=request.user_id,
                text=request.text,
                image_bytes=request.image_bytes,
                image_mime_type=request.image_mime_type,
                source_message_id=request.source_message_id,
                channel_id=request.channel_id,
                occurred_at=request.occurred_at,
                deadline_at=now + self._turn_timeout,
                execution_mode=ToolExecutionMode.LIVE,
            )
        )
        if result.termination is HarnessTermination.WAITING_USER_CONFIRMATION:
            return (
                "这项操作会更改或清空已保存的数据，需要你再次明确确认。"
                "请回复确认执行，或回复取消。"
            )[: self._max_reply_chars]
        if (
            result.termination is not HarnessTermination.FINAL_RESPONSE
            or result.final_text is None
            or not result.final_text.strip()
        ):
            raise HarnessReplyError(
                f"Harness Turn ended without a reply: {result.termination.value}"
            )
        return result.final_text.strip()[: self._max_reply_chars]

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
