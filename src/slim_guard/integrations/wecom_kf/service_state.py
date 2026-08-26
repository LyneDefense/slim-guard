from __future__ import annotations

from enum import IntEnum


class WeComServiceState(IntEnum):
    """States defined by the WeCom Customer Service conversation API."""

    UNPROCESSED = 0
    SMART_ASSISTANT = 1
    WAITING_POOL = 2
    HUMAN = 3
    ENDED = 4


AGENT_SENDABLE_STATES = frozenset(
    {WeComServiceState.UNPROCESSED, WeComServiceState.SMART_ASSISTANT}
)
