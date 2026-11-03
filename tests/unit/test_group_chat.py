from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from slim_guard.agents.conversation.contracts import (
    ConversationDecision,
    ConversationMessageProposal,
)
from slim_guard.group_chat.composer import OutgoingTextMessage, compose_messages, project_cards
from slim_guard.group_chat.contracts import ParticipantRole, RenderKind
from slim_guard.group_chat.routing import ConversationRouter


def test_planner_must_explicitly_decide_whether_system_notice_requires_response():
    with pytest.raises(ValidationError, match="requires_response"):
        ConversationDecision.model_validate(
            {
                "messages": [
                    {
                        "participant": "system_assistant",
                        "text": "这道菜叫什么？",
                        "notice_kind": "operation_clarification",
                    }
                ]
            }
        )
    result = ConversationDecision.model_validate(
        {
            "messages": [
                {
                    "participant": "system_assistant",
                    "text": "这道菜叫什么？",
                    "requires_response": True,
                    "notice_kind": "operation_clarification",
                }
            ]
        }
    )
    assert result.messages[0].requires_response is True


def test_system_question_presentation_survives_routing_and_composition():
    routed = ConversationRouter().route(
        proposals=(
            ConversationMessageProposal(
                participant="system_assistant",
                text="请问这是哪一道菜？",
                notice_text="这道菜叫什么？",
                requires_response=True,
                notice_kind="operation_clarification",
            ),
        ),
    )
    message = routed.messages[0]
    result = compose_messages(
        turn_id="turn1",
        text_messages=(
            OutgoingTextMessage(
                participant=message.participant,
                text=message.text,
                notice_text=message.notice_text,
                requires_response=message.requires_response,
            ),
        ),
    )
    assert result[0].requires_response is True
    assert result[0].notice_text == "这道菜叫什么？"
    assert result[0].text == "请问这是哪一道菜？"


def test_conversation_router_does_not_suppress_coach_during_clarification() -> None:
    result = ConversationRouter().route(
        proposals=(
            ConversationMessageProposal(
                participant="system_assistant",
                text="请确认这道菜是什么。",
                notice_kind="operation_clarification",
                requires_response=True,
            ),
            ConversationMessageProposal(participant="coach", text="这顿配了蔬菜，不错。"),
        ),
    )

    assert [item.participant for item in result.messages] == [
        ParticipantRole.SYSTEM_ASSISTANT,
        ParticipantRole.COACH,
    ]


def test_system_notice_is_model_selected_not_a_copy_of_the_whole_core_draft() -> None:
    result = ConversationRouter().route(
        proposals=(
            ConversationMessageProposal(
                participant="system_assistant",
                text="已记录这顿饭。",
                notice_kind="operation_result",
                requires_response=False,
            ),
            ConversationMessageProposal(participant="coach", text="有荤有素，搭配不错。"),
        ),
    )

    assert result.system_messages[0].text == "已记录这顿饭。"
    assert result.messages[1].participant is ParticipantRole.COACH
    assert result.messages[1].text == "有荤有素，搭配不错。"
    assert result.suppressed_reason is None


def test_conversation_router_does_not_route_based_on_tool_failure_alone() -> None:
    result = ConversationRouter().route(
        proposals=(
            ConversationMessageProposal(
                participant="system_assistant",
                text="这次记录没有成功，请确认菜品。",
                notice_kind="operation_clarification",
                requires_response=True,
            ),
            ConversationMessageProposal(participant="coach", text="确认好我们再继续。"),
        ),
    )

    assert len(result.coach_messages) == 1


@pytest.mark.parametrize("text", ["你好。", "今天过得怎么样？", "先把作息稳定下来。"])
def test_coach_only_plan_routes_and_composes_without_an_injected_system_message(text):
    plan = ConversationDecision.model_validate(
        {"messages": [{"participant": "coach", "text": text}]}
    )
    routed = ConversationRouter().route(proposals=plan.messages)
    assert routed.system_messages == ()
    assert routed.suppressed_reasons == ()
    messages = compose_messages(
        turn_id="greeting",
        text_messages=tuple(
            OutgoingTextMessage(participant=m.participant, text=m.text) for m in routed.messages
        ),
    )
    assert [(m.participant, m.text) for m in messages] == [(ParticipantRole.COACH, text)]


def test_system_notices_need_operation_purpose_but_no_system_message_is_required():
    with pytest.raises(ValidationError, match="operation purpose"):
        ConversationMessageProposal(
            participant="system_assistant", text="你好。", requires_response=False
        )
    with pytest.raises(ValidationError, match="Notice metadata"):
        ConversationMessageProposal(
            participant="coach", text="你好。", notice_kind="operation_result"
        )
    with pytest.raises(ValidationError, match="clarification"):
        ConversationMessageProposal(
            participant="system_assistant",
            text="请提供日期。",
            notice_kind="operation_clarification",
            requires_response=False,
        )
    with pytest.raises(ValidationError):
        ConversationDecision(messages=())


@pytest.mark.parametrize("role", ["coach", "system_assistant"])
def test_router_rejects_foreign_references_without_manufacturing_a_system_message(role):
    proposal = ConversationMessageProposal(
        participant=role,
        text="回复。",
        source_refs=("foreign",),
        **(
            {"requires_response": False, "notice_kind": "operation_result"}
            if role == "system_assistant"
            else {}
        ),
    )
    routed = ConversationRouter().route(proposals=(proposal,), allowed_source_refs=("allowed",))
    assert not routed.messages
    assert routed.suppressed_reasons == (f"{role}_source_ref_not_allowed",)


def test_group_chat_composer_keeps_coach_and_system_as_distinct_messages() -> None:
    messages = compose_messages(
        turn_id="turn-1",
        text_messages=(
            OutgoingTextMessage(
                participant=ParticipantRole.COACH,
                text="行，这顿搭配挺好。",
                style_profile_version="doctor_v1",
            ),
            OutgoingTextMessage(
                participant=ParticipantRole.SYSTEM_ASSISTANT,
                text="已记录今日晚餐。",
                source_refs=("tool-1",),
            ),
        ),
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
