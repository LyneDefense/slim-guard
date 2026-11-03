"""Validate Conversation Agent message proposals against Harness authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from slim_guard.group_chat.contracts import ParticipantRole

if TYPE_CHECKING:
    from collections.abc import Sequence

    from slim_guard.agents.conversation.contracts import ConversationMessageProposal


@dataclass(frozen=True, slots=True)
class RoutedMessage:
    participant: ParticipantRole
    text: str
    source_refs: tuple[str, ...] = ()
    requires_response: bool = False
    notice_text: str | None = None
    notice_kind: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingResult:
    messages: tuple[RoutedMessage, ...]
    suppressed_reasons: tuple[str, ...] = ()

    @property
    def system_messages(self) -> tuple[RoutedMessage, ...]:
        return tuple(
            message
            for message in self.messages
            if message.participant is ParticipantRole.SYSTEM_ASSISTANT
        )

    @property
    def coach_messages(self) -> tuple[RoutedMessage, ...]:
        return tuple(
            message for message in self.messages if message.participant is ParticipantRole.COACH
        )

    @property
    def suppressed_reason(self) -> str | None:
        return ";".join(self.suppressed_reasons) or None


class ConversationRouter:
    """Validate message provenance, not the user's business intent.

    This is a contract/safety boundary, not a scenario router. Pending questions
    and failed operations are facts supplied to the Conversation Agent; neither
    status alone decides whether the coach may speak.
    """

    def route(
        self,
        *,
        proposals: Sequence[ConversationMessageProposal],
        allowed_source_refs: tuple[str, ...] = (),
    ) -> RoutingResult:
        allowed = set(allowed_source_refs)
        routed: list[RoutedMessage] = []
        suppressed: list[str] = []
        system_seen = False
        seen: set[tuple[str, str]] = set()

        for proposal in proposals:
            refs = proposal.source_refs
            if any(ref not in allowed for ref in refs):
                suppressed.append(f"{proposal.participant}_source_ref_not_allowed")
                continue
            identity = (proposal.participant, proposal.text)
            if identity in seen:
                suppressed.append("duplicate_message")
                continue
            seen.add(identity)
            if proposal.participant == "system_assistant":
                if system_seen:
                    suppressed.append("duplicate_system_message")
                    continue
                system_seen = True
                routed.append(
                    RoutedMessage(
                        participant=ParticipantRole.SYSTEM_ASSISTANT,
                        text=proposal.text,
                        source_refs=refs,
                        requires_response=proposal.requires_response,
                        notice_text=proposal.notice_text,
                        notice_kind=proposal.notice_kind,
                    )
                )
                continue

            routed.append(
                RoutedMessage(
                    participant=ParticipantRole.COACH,
                    text=proposal.text,
                    source_refs=refs,
                )
            )

        return RoutingResult(messages=tuple(routed), suppressed_reasons=tuple(suppressed))


__all__ = ["ConversationRouter", "RoutedMessage", "RoutingResult"]
