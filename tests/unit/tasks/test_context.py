from slim_guard.harness.context import ContextCompiler
from slim_guard.orchestration.evidence import EvidenceAuthority, EvidenceBuilder


async def test_task_quotes_reach_core_and_nutrition_without_promoting_model_interpretation():
    working = {
        "conversation_tasks": [
            {
                "evidence": [
                    {
                        "source_item_id": "older-user-message",
                        "excerpt": "荷包蛋",
                        "interpretation": "不能当成用户说过的健康推断",
                    }
                ]
            }
        ]
    }
    assert ContextCompiler._evidence_item_ids(working) == ("older-user-message",)
    packet = await EvidenceBuilder().build(
        turn_id="current",
        user_request="继续",
        professional_question="评价午餐",
        current_items=(),
        authoritative_context={"working_memory": working},
        allowed_evidence_refs=("older-user-message",),
    )
    assert len(packet.items) == 1
    assert packet.items[0].authority is EvidenceAuthority.USER_REPORTED
    assert packet.items[0].content == {"text": "荷包蛋"}
    assert not packet.missing_information


async def test_multiple_quotes_from_same_message_are_not_lost():
    packet = await EvidenceBuilder().build(
        turn_id="current",
        user_request="继续",
        professional_question="评价午餐",
        current_items=(),
        authoritative_context={
            "working_memory": {
                "conversation_tasks": [
                    {
                        "evidence": [
                            {"source_item_id": "answer", "excerpt": "荷包蛋"},
                            {"source_item_id": "answer", "excerpt": "烧茄子"},
                            {"source_item_id": "answer", "excerpt": "荷包蛋"},
                        ]
                    }
                ],
            }
        },
    )
    assert len(packet.items) == 1
    assert packet.items[0].content == {"excerpts": ["荷包蛋", "烧茄子"]}
