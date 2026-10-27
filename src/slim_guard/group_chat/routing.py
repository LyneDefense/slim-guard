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


@dataclass(frozen=True, slots=True)
class RoutingResult:
    messages: tuple[RoutedMessage, ...]
    suppressed_reasons: tuple[str, ...] = ()

    @property
    def system_text(self) -> str:
        return next(
            message.text
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
    """Validate ordering and provenance while preserving authoritative status.

    This is a contract/safety boundary, not a scenario router. Pending questions
    and failed operations are facts supplied to the Conversation Agent; neither
    status alone decides whether the coach may speak.
    """

    def route(
        self,
        *,
        system_text: str,
        proposals: Sequence[ConversationMessageProposal],
        system_source_refs: tuple[str, ...] = (),
        allowed_source_refs: tuple[str, ...] = (),
    ) -> RoutingResult:
        authoritative_system_text = system_text.strip()
        if not authoritative_system_text:
            raise ValueError("The system assistant must have an authoritative response")

        allowed = set(allowed_source_refs)
        system_refs = tuple(ref for ref in system_source_refs if ref in allowed)
        routed: list[RoutedMessage] = []
        suppressed: list[str] = []
        system_seen = False

        for proposal in proposals:
            if proposal.participant == "system_assistant":
                if system_seen:
                    suppressed.append("duplicate_system_message")
                    continue
                system_seen = True
                if proposal.text.strip() != authoritative_system_text:
                    # Presentation planning cannot change a verified operation
                    # result, system fact, or required clarification.
                    suppressed.append("system_text_kept_authoritative")
                routed.append(
                    RoutedMessage(
                        participant=ParticipantRole.SYSTEM_ASSISTANT,
                        text=authoritative_system_text,
                        source_refs=system_refs,
                    )
                )
                continue

            candidate = proposal.text.strip()
            refs = tuple(dict.fromkeys(ref.strip() for ref in proposal.source_refs if ref.strip()))
            if any(ref not in allowed for ref in refs):
                suppressed.append("coach_source_ref_not_allowed")
                continue
            if not candidate or candidate == authoritative_system_text:
                suppressed.append("duplicate_system_text")
                continue
            routed.append(
                RoutedMessage(
                    participant=ParticipantRole.COACH,
                    text=candidate,
                    source_refs=refs,
                )
            )

        if not system_seen:
            # Defensive fallback for non-model callers. The typed output contract
            # normally requires exactly one system message.
            routed.append(
                RoutedMessage(
                    participant=ParticipantRole.SYSTEM_ASSISTANT,
                    text=authoritative_system_text,
                    source_refs=system_refs,
                )
            )
            suppressed.append("system_message_added_by_harness")

        return RoutingResult(messages=tuple(routed), suppressed_reasons=tuple(suppressed))


__all__ = ["ConversationRouter", "RoutedMessage", "RoutingResult"]
