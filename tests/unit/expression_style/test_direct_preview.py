import json

from slim_guard.agent.assembly.style_preview import build_coach_preview
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.expression_style.package import Guide, make_package
from slim_guard.expression_style.trainer.contracts import TestCase as ConversationCase

PASS = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


async def test_multi_turn_preview_uses_actual_replies_not_ideal_drafts():
    gateway = ScriptedModelGateway(
        [
            response({"text": "散步也不喜欢？"}),
            response(PASS),
            response({"text": "那就散步。"}),
            response(PASS),
        ]
    )
    preview = build_coach_preview(gateway, "test", 120)
    case = ConversationCase(
        id="multi",
        family="exercise",
        user_input="我不爱运动",
        follow_up_inputs=("散步还行",),
        source_text="禁止泄露的理想答案",
    )
    outcome = await preview.run(
        case=case,
        style=make_package("doctor", "v", "医生", Guide(summary="简短")).runtime_snapshot(),
    )
    assert outcome["passed"] and outcome["model_calls"] == 4
    assert [c["step"] for c in outcome["checks"]] == [1, 2]
    evidence = json.loads(gateway.requests[2].messages[-1].content)["evidence"]
    assert evidence["context"]["recent_dialogue"][0]["messages"][1]["content"] == "散步也不喜欢？"
    assert evidence["context"]["current_user_input"]["text"] == "散步还行"
    assert outcome["history"][-1]["content"] == "散步也不喜欢？"
    assert all(
        "禁止泄露的理想答案" not in json.dumps([m.content for m in r.messages])
        for r in gateway.requests
    )
    assert all(not r.tools for r in gateway.requests)
    assert {r.output_schema_name for r in gateway.requests} == {"CoachReply", "ReplyCheck"}
