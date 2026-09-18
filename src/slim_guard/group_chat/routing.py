"""Fail-closed validation for the LLM's coach-message proposal.

The model may propose a relational coach sentence, but it never chooses a tool
state or card sender. The router keeps the authoritative system text and drops
unsafe or duplicate coach candidates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CoachCandidate:
    text: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingResult:
    system_text: str
    coach: CoachCandidate | None
    suppressed_reason: str | None = None


class ParticipantRouter:
    """Validate a proposed coach block without keyword-based scenario enums."""

    def route(
        self,
        *,
        system_text: str,
        coach_text: str | None,
        coach_source_refs: tuple[str, ...] = (),
        allowed_source_refs: tuple[str, ...] = (),
        pending_clarification: bool = False,
    ) -> RoutingResult:
        system = system_text.strip()
        if not system:
            raise ValueError("The system assistant must have an authoritative response")
        candidate = (coach_text or "").strip()
        if not candidate:
            return RoutingResult(system_text=system, coach=None)
        if pending_clarification:
            return RoutingResult(
                system_text=system,
                coach=None,
                suppressed_reason="conflicts_with_pending_clarification",
            )
        allowed = set(allowed_source_refs)
        refs = tuple(dict.fromkeys(item.strip() for item in coach_source_refs if item.strip()))
        if any(item not in allowed for item in refs):
            return RoutingResult(
                system_text=system,
                coach=None,
                suppressed_reason="coach_source_ref_not_allowed",
            )
        if candidate == system:
            return RoutingResult(
                system_text=system,
                coach=None,
                suppressed_reason="duplicate_system_text",
            )
        return RoutingResult(
            system_text=system,
            coach=CoachCandidate(text=candidate, source_refs=refs),
        )


__all__ = ["CoachCandidate", "ParticipantRouter", "RoutingResult"]
