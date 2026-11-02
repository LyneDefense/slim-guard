"""Contracts and composition helpers for SlimGuard's three-party chat."""

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
