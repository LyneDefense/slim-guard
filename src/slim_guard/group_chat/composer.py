"""Compose visible group-chat messages from authoritative turn results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from slim_guard.group_chat.contracts import CardPayload, ChatMessage, ParticipantRole, RenderKind

if TYPE_CHECKING:
    from slim_guard.harness.tool_calls import ToolCallOutcome

_CARD_TOOLS = {
    "record_meal": "meal_record",
    "record_weight": "weight_record",
    "record_body_fat": "body_fat_record",
    "record_exercise": "exercise_record",
}


def compose_messages(
    *,
    turn_id: str,
    system_text: str,
    coach_text: str | None,
    coach_source_refs: tuple[str, ...] = (),
    style_profile_version: str | None = None,
    tool_outcomes: tuple[ToolCallOutcome, ...] = (),
    created_at: datetime | None = None,
) -> tuple[ChatMessage, ...]:
    now = created_at or datetime.now(UTC)
    messages: list[ChatMessage] = []
    if coach_text and coach_text.strip():
        messages.append(
            ChatMessage(
                id=f"chat-{uuid4()}",
                turn_id=turn_id,
                sequence=0,
                participant=ParticipantRole.COACH,
                kind=RenderKind.TEXT,
                text=coach_text.strip(),
                source_refs=coach_source_refs,
                style_profile_version=style_profile_version,
                created_at=now,
            )
        )
    messages.append(
        ChatMessage(
            id=f"chat-{uuid4()}",
            turn_id=turn_id,
            sequence=len(messages),
            participant=ParticipantRole.SYSTEM_ASSISTANT,
            kind=RenderKind.TEXT,
            text=system_text.strip(),
            created_at=now,
        )
    )
    for card in project_cards(tool_outcomes):
        messages.append(
            ChatMessage(
                id=f"chat-{uuid4()}",
                turn_id=turn_id,
                sequence=len(messages),
                participant=ParticipantRole.SYSTEM_ASSISTANT,
                kind=RenderKind.CARD,
                card=card,
                created_at=now,
            )
        )
    return tuple(messages)


def project_cards(outcomes: tuple[ToolCallOutcome, ...]) -> tuple[CardPayload, ...]:
    cards: list[CardPayload] = []
    for outcome in outcomes:
        execution = outcome.execution
        card_type = _CARD_TOOLS.get(execution.tool_name)
        if card_type is None or execution.result.status.value != "succeeded":
            continue
        cards.append(
            CardPayload(
                card_type=card_type,
                status="success",
                data=_safe_card_data(execution.result.output),
                source_execution_id=execution.tool_call_id,
            )
        )
    return tuple(cards)


def _safe_card_data(value: dict[str, Any]) -> dict[str, Any]:
    """Keep tool-owned structured data while excluding internal control fields."""

    return {
        key: item
        for key, item in value.items()
        if key not in {"source_ids", "trusted_evidence_item_ids"}
    }


__all__ = ["compose_messages", "project_cards"]
