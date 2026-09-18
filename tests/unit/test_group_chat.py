from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from slim_guard.group_chat.composer import compose_messages, project_cards
from slim_guard.group_chat.contracts import ParticipantRole, RenderKind
from slim_guard.group_chat.routing import ParticipantRouter


def test_participant_router_defaults_to_system_when_clarification_is_pending() -> None:
    result = ParticipantRouter().route(
        system_text="请确认这道菜是什么。",
        coach_text="这顿搭配挺好。",
        pending_clarification=True,
    )

    assert result.coach is None
    assert result.suppressed_reason == "conflicts_with_pending_clarification"


def test_group_chat_composer_keeps_coach_and_system_as_distinct_messages() -> None:
    messages = compose_messages(
        turn_id="turn-1",
        system_text="已记录今日晚餐。",
        coach_text="行，这顿搭配挺好。",
        style_profile_version="doctor_v1",
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )

    assert [message.participant for message in messages] == [
        ParticipantRole.COACH,
        ParticipantRole.SYSTEM_ASSISTANT,
    ]
    assert [message.kind for message in messages] == [RenderKind.TEXT, RenderKind.TEXT]
    assert messages[0].style_profile_version == "doctor_v1"
    assert messages[1].text == "已记录今日晚餐。"


def test_meal_card_projects_only_user_facing_fields() -> None:
    result = SimpleNamespace(
        status=SimpleNamespace(value="succeeded"),
        output={
            "created": True,
            "record_id": "internal-record-id",
            "occurred_at": "2026-09-18T14:57:33+00:00",
            "meal_type": "lunch",
            "foods": [
                {"name": "米饭", "portion": None},
                {"name": "鸡胸肉", "portion": "一份"},
            ],
        },
    )
    execution = SimpleNamespace(
        tool_name="record_meal",
        tool_call_id="tool-call-1",
        result=result,
    )
    outcome = SimpleNamespace(execution=execution)

    cards = project_cards((outcome,))
    assert cards[0].data == {
        "meal_type": "午餐",
        "foods": ["米饭", "鸡胸肉（一份）"],
    }
