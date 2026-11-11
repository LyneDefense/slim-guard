import json

import pytest

import slim_guard.agents.coach.agent as coach_agent
from slim_guard.agent.assembly.style_preview import build_coach_preview
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.expression_style.package import Guide, make_package
from slim_guard.expression_style.trainer.contracts import TestCase as ConversationCase

PASS = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


def rejection(text, *, dimension="expression", severity="repairable"):
    return {
        **PASS,
        f"{dimension}_passed": False,
        "issues": [
            {
                "dimension": dimension,
                "severity": severity,
                "code": "synthetic_issue",
                "explanation": "对应维度未通过",
                "source_excerpt": "",
                "output_excerpt": text,
                "repair_requirement": "修复该维度的具体问题",
            }
        ],
    }


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


@pytest.mark.parametrize("severity", ["repairable", "blocking"])
async def test_style_only_preview_returns_first_reviewed_reply_without_repair(severity):
    text = "可以先歇一歇。"
    gateway = ScriptedModelGateway(
        [
            response({"text": text}),
            response(rejection(text, severity=severity)),
        ]
    )
    preview = build_coach_preview(gateway, "test", 120)
    outcome = await preview.run(
        case=ConversationCase(id="style", family="exercise", user_input="有点累"),
        style=make_package("doctor", "v", "医生", Guide(summary="简短")).runtime_snapshot(),
    )
    assert outcome["text"] == text and outcome["passed"]
    assert outcome["style_advisory"] and outcome["repairs"] == 0
    assert outcome["model_calls"] == 2
    assert len(outcome["checks"]) == 1 and outcome["checks"][0]["adopted"]
    gateway.assert_exhausted()


@pytest.mark.parametrize("dimension", ["fidelity", "role"])
async def test_preview_repairs_safety_before_adopting_style_advisory(dimension):
    bad, safe = "越界或无依据的回答。", "可以先休息。"
    gateway = ScriptedModelGateway(
        [
            response({"text": bad}),
            response(rejection(bad, dimension=dimension)),
            response({"text": safe}),
            response(rejection(safe)),
        ]
    )
    outcome = await build_coach_preview(gateway, "test", 120).run(
        case=ConversationCase(id="safety", family="exercise", user_input="有点累"),
        style=make_package("doctor", "v", "医生", Guide(summary="简短")).runtime_snapshot(),
    )
    assert outcome["text"] == safe and outcome["passed"]
    assert outcome["style_advisory"] and outcome["repairs"] == 1
    assert outcome["model_calls"] == 4
    assert [item["adopted"] for item in outcome["checks"]] == [False, True]
    gateway.assert_exhausted()


def test_preview_cache_signature_changes_with_dialogue_policy(monkeypatch):
    gateway = ScriptedModelGateway([])
    current = build_coach_preview(gateway, "test", 120).signature
    monkeypatch.setattr(coach_agent, "DIALOGUE_POLICY_VERSION", "coach-dialogue-v1")
    previous = build_coach_preview(gateway, "test", 120).signature
    assert current != previous
