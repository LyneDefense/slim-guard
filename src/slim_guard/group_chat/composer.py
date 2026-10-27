"""Compose visible group-chat messages from authoritative turn results."""

from __future__ import annotations

from dataclasses import dataclass
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

_MEAL_TYPE_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "加餐",
    "unspecified": "餐食",
}

_CONDITION_LABELS = {
    "fasting": "空腹",
    "post_meal": "餐后",
    "unspecified": None,
}


@dataclass(frozen=True, slots=True)
class OutgoingTextMessage:
    participant: ParticipantRole
    text: str
    source_refs: tuple[str, ...] = ()
    style_profile_version: str | None = None


def compose_messages(
    *,
    turn_id: str,
    text_messages: tuple[OutgoingTextMessage, ...],
    tool_outcomes: tuple[ToolCallOutcome, ...] = (),
    created_at: datetime | None = None,
) -> tuple[ChatMessage, ...]:
    now = created_at or datetime.now(UTC)
    messages: list[ChatMessage] = []
    if not any(item.participant is ParticipantRole.SYSTEM_ASSISTANT for item in text_messages):
        raise ValueError("Outgoing message sequence must include the system assistant")
    for item in text_messages:
        if not item.text.strip():
            continue
        messages.append(
            ChatMessage(
                id=f"chat-{uuid4()}",
                turn_id=turn_id,
                sequence=len(messages),
                participant=item.participant,
                kind=RenderKind.TEXT,
                text=item.text.strip(),
                source_refs=item.source_refs,
                style_profile_version=item.style_profile_version,
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
                source_refs=(card.source_execution_id,) if card.source_execution_id else (),
                created_at=now,
            )
        )
    return tuple(messages)


def project_cards(outcomes: tuple[ToolCallOutcome, ...]) -> tuple[CardPayload, ...]:
    cards: list[CardPayload] = []
    seen: set[str] = set()
    for outcome in outcomes:
        execution = outcome.execution
        operation = execution.result.operation
        tool_name = operation.tool_name if operation is not None else execution.tool_name
        identity = operation.operation_id if operation is not None else execution.tool_call_id
        card_type = _CARD_TOOLS.get(tool_name)
        if card_type is None or execution.result.status.value != "succeeded":
            continue
        if identity in seen:
            continue
        seen.add(identity)
        data = _project_card_data(tool_name, execution.result.output)
        cards.append(
            CardPayload(
                card_type=card_type,
                status="success",
                data=data,
                source_execution_id=execution.tool_call_id,
            )
        )
    return tuple(cards)


def _project_card_data(tool_name: str, value: dict[str, Any]) -> dict[str, Any]:
    """Project tool receipts into a small, user-facing card contract.

    Tool outputs intentionally contain persistence IDs, timestamps and idempotency
    metadata. Those fields remain available in Trace but must never be rendered as
    chat content.
    """

    if tool_name == "record_meal":
        foods = value.get("foods", ())
        visible_foods: list[str] = []
        if isinstance(foods, list):
            for food in foods:
                if not isinstance(food, dict):
                    continue
                name = food.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                portion = food.get("portion")
                label = name.strip()
                if isinstance(portion, str) and portion.strip():
                    label = f"{label}（{portion.strip()}）"
                visible_foods.append(label)
        result: dict[str, Any] = {
            "meal_type": _MEAL_TYPE_LABELS.get(str(value.get("meal_type")), "餐食"),
            "foods": visible_foods,
        }
        note = value.get("note")
        if isinstance(note, str) and note.strip():
            result["note"] = note.strip()
        return result

    if tool_name == "record_weight":
        result = {}
        for key in ("weight_kg", "original_value"):
            item = value.get(key)
            if item is not None:
                result[key] = item
        condition = _CONDITION_LABELS.get(str(value.get("condition")))
        if condition is not None:
            result["condition"] = condition
        return result

    if tool_name == "record_body_fat":
        item = value.get("body_fat_percent")
        return {"body_fat_percent": item} if item is not None else {}

    if tool_name == "record_exercise":
        result = {}
        for key in (
            "activity_name",
            "duration_minutes",
            "steps",
            "distance_meters",
            "reported_energy_kcal",
            "note",
        ):
            item = value.get(key)
            if item is not None and item != "":
                result[key] = item
        return result

    return {}


__all__ = ["OutgoingTextMessage", "compose_messages", "project_cards"]
