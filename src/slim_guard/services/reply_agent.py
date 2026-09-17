from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReplyRequest:
    user_id: str
    nickname: str | None
    text: str | None = None
    image_bytes: bytes | None = None
    image_mime_type: str | None = None
    source_message_id: str | None = None
    channel_id: str | None = None
    occurred_at: datetime | None = None
    trace_id: str | None = None


class ReplyAgentProtocol(Protocol):
    async def generate_reply(self, request: ReplyRequest) -> str: ...


class StaticReplyAgent:
    """Deterministic infrastructure fallback when the Core runtime is unavailable."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def generate_reply(self, request: ReplyRequest) -> str:
        return self.reply
