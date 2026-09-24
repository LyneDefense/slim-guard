from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from slim_guard.group_chat.composer import compose_messages, project_cards
from slim_guard.group_chat.contracts import ParticipantRole, RenderKind
from slim_guard.group_chat.routing import ConversationRouter


def test_conversation_router_defaults_to_system_when_clarification_is_pending() -> None:
    result = ConversationRouter().route(
        system_text="请确认这道菜是什么。",
        coach_text="这顿搭配挺好。",
        pending_clarification=True,
    )

    assert result.coach is None
    assert result.suppressed_reason == "conflicts_with_pending_clarification"


def test_conversation_router_keeps_core_system_text_authoritative() -> None:
    result = ConversationRouter().route(
        system_text="记上了这顿饭。有荤有素，不错。",
        routed_system_text="已记录这顿饭。",
        coach_text="有荤有素，搭配不错。",
    )

    assert result.system_text == "记上了这顿饭。有荤有素，不错。"
    assert result.coach is not None
    assert result.coach.text == "有荤有素，搭配不错。"


def test_conversation_router_suppresses_coach_after_tool_failure() -> None:
    result = ConversationRouter().route(
        system_text="这次记录没有成功，请确认菜品。",
        coach_text="这顿搭配挺好。",
        tool_failed=True,
    )

    assert result.coach is None
    assert result.suppressed_reason == "conflicts_with_failed_tool"


def test_group_chat_composer_keeps_coach_and_system_as_distinct_messages() -> None:
    messages = compose_messages(
        turn_id="turn-1",
        system_text="已记录今日晚餐。",
        system_source_refs=("tool-1",),
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
    assert messages[1].source_refs == ("tool-1",)


def test_meal_card_projects_only_user_facing_fields() -> None:
    result = SimpleNamespace(
        operation=None,
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


def test_resumed_operation_projects_one_business_card_per_turn() -> None:
    from slim_guard.tools.contracts import ToolExecution, ToolOperationReceipt, ToolResult

    result = ToolResult.success(
        output={
            "record_id": "meal-1",
            "meal_type": "lunch",
            "foods": [{"name": "烧茄子"}],
        }
    ).model_copy(
        update={
            "operation": ToolOperationReceipt(
                operation_id="op-1",
                tool_name="record_meal",
                status="succeeded",
            )
        }
    )
    outcomes = tuple(
        SimpleNamespace(
            execution=ToolExecution(
                tool_call_id=call,
                tool_name=name,
                result=result,
            )
        )
        for call, name in [("call-1", "record_meal"), ("call-2", "resume_tool_operation")]
    )
    cards = project_cards(outcomes)
    assert len(cards) == 1
    assert cards[0].data["foods"] == ["烧茄子"]
    assert len(project_cards(outcomes[1:])) == 1
