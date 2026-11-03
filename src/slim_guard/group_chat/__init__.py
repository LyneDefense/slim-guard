"""Chat messages and operational notices; the coach is the only conversational role."""

from slim_guard.group_chat.contracts import (
    CardPayload,
    ChatMessage,
    ParticipantRole,
    RenderKind,
)
from slim_guard.group_chat.role_policy import (
    COACH_ROLE_POLICY,
    COACH_ROLE_VIOLATION,
    ROLE_POLICY_VERSION,
)

__all__ = [
    "COACH_ROLE_POLICY",
    "COACH_ROLE_VIOLATION",
    "ROLE_POLICY_VERSION",
    "CardPayload",
    "ChatMessage",
    "ParticipantRole",
    "RenderKind",
]
