import json

import pytest

from slim_guard.agent.assembly.coach_sandbox import CoachSandbox
from slim_guard.agent_models.errors import ModelTransportError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse, NormalizedToolCall
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.persona import DialogueExample, PersonaContent
from slim_guard.style_management.studio.model import StyleStudioModel
from slim_guard.style_management.studio.ports import StudioModelError

PASS = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


def source(source_id="persona", **changes):
    return {
        "source_id": source_id,
        "role_passed": True,
        "safety_passed": True,
        "issues": [],
        **changes,
    }


def content(**changes):
    return PersonaContent(expression="简短、直接，需要解释时说清楚。", **changes)


def model(gateway, **changes):
    return StyleStudioModel(
        gateway=gateway, preview=CoachSandbox(gateway, "test", 30), model="test", **changes
    )


async def test_check_covers_persona_and_independent_multi_turn_example():
    sample = DialogueExample(
        id="greeting",
        messages=(
            DialogueMessage(role="user", content="你好"),
            DialogueMessage(role="assistant", content="来了"),
            DialogueMessage(role="user", content="没什么特别的"),
            DialogueMessage(role="assistant", content="那就照常"),
        ),
    )
    value = content(examples=(sample,))
    gateway = ScriptedModelGateway([response({"checks": [source(), source("example:greeting")]})])
    result = await model(gateway).check(content=value)
    assert result["passed"]
    request = gateway.requests[0]
    assert request.tool_choice.value == "none" and request.tools == ()
    data = json.loads(request.messages[1].content)
    assert len(data["sources"][1]["messages"]) == 4
    assert "original_response" not in str(data) and "rule_ids" not in str(data)
    assert value.examples == (sample,)


async def test_missing_or_duplicate_checks_cannot_authorize_publication():
    gateway = ScriptedModelGateway(
        [
            response({"checks": [source("invented")]}),
            response({"checks": [source(), source()]}),
        ]
    )
    with pytest.raises(StudioModelError, match="不完整"):
        await model(gateway).check(content=content())
    assert len(gateway.requests) == 2
    assert "完整覆盖" in gateway.requests[1].messages[-1].content


async def test_role_failure_is_explained_and_does_not_become_a_pass():
    issue = {"dimension": "role", "code": "records", "explanation": "不能承担记录职责"}
    gateway = ScriptedModelGateway(
        [response({"checks": [source(role_passed=False, issues=[issue])]})]
    )
    result = await model(gateway).check(content=PersonaContent(expression="帮用户记录所有事情"))
    assert not result["passed"] and result["checks"][0]["issues"] == [issue]


async def test_contradictory_flags_and_invalid_json_fail_closed():
    gateway = ScriptedModelGateway(
        [
            response({"checks": [source(safety_passed=False)]}),
            ModelResponse(message=ModelMessage(role="assistant", content="not json")),
        ]
    )
    with pytest.raises(StudioModelError):
        await model(gateway).check(content=content())


async def test_unexpected_tools_and_provider_errors_fail_closed():
    tool = NormalizedToolCall(id="t", name="record_weight", arguments={"weight": 50})
    gateway = ScriptedModelGateway(
        [
            ModelResponse(message=ModelMessage(role="assistant", tool_calls=(tool,))),
        ]
    )
    with pytest.raises(StudioModelError, match="工具调用"):
        await model(gateway).check(content=content())
    gateway = ScriptedModelGateway([ModelTransportError("provider unavailable")])
    with pytest.raises(StudioModelError, match="不可用"):
        await model(gateway).organize(content=content())


async def test_blank_check_and_oversized_prompt_make_no_model_calls():
    gateway = ScriptedModelGateway([])
    with pytest.raises(ValueError, match="表达设定"):
        await model(gateway).check(content=PersonaContent())
    sample = DialogueExample(
        id="long",
        messages=(
            DialogueMessage(role="user", content="问" * 4000),
            DialogueMessage(role="assistant", content="答" * 4000),
        ),
    )
    with pytest.raises(ValueError, match="提示预算"):
        await model(gateway).preview(
            content=content(examples=(sample,)), user_input="hi", history=()
        )
    assert gateway.requests == []


async def test_organize_only_returns_suggestion_without_mutating_input():
    value = content()
    gateway = ScriptedModelGateway([response({"expression": "自然直接。", "avoid": "少说套话。"})])
    suggestion = await model(gateway).organize(content=value)
    assert suggestion == {"expression": "自然直接。", "avoid": "少说套话。"}
    assert value.expression == "简短、直接，需要解释时说清楚。"
    assert (
        model(gateway).signature
        != StyleStudioModel(
            gateway=gateway, preview=CoachSandbox(gateway, "another", 30), model="another"
        ).signature
    )


async def test_preview_uses_actual_history_and_shared_live_safety_review():
    gateway = ScriptedModelGateway([response({"text": "那就散步。"}), response(PASS)])
    value = content()
    history = (
        DialogueMessage(role="user", content="我不喜欢运动"),
        DialogueMessage(role="assistant", content="散步也不喜欢？"),
    )
    result = await model(gateway).preview(content=value, user_input="散步还行", history=history)
    assert result["text"] == "那就散步。" and result["passed"]
    assert result["content_hash"] == value.content_hash
    assert [r.output_schema_name for r in gateway.requests] == ["CoachReply", "ReplyCheck"]
    evidence = json.loads(gateway.requests[0].messages[-1].content)["evidence"]
    assert evidence["context"]["recent_dialogue"][0]["messages"][1]["content"] == "散步也不喜欢？"
    assert evidence["context"]["authoritative_facts"] == {}
    assert all(not r.tools for r in gateway.requests)


async def test_preview_style_only_advisory_keeps_reviewed_reply_in_two_calls():
    text = "那就先歇会儿。"
    gateway = ScriptedModelGateway(
        [
            response({"text": text}),
            response(
                {
                    **PASS,
                    "expression_passed": False,
                    "issues": [
                        {
                            "dimension": "expression",
                            "severity": "repairable",
                            "code": "tone_preference",
                            "explanation": "表达还可以更直接",
                            "source_excerpt": "",
                            "output_excerpt": text,
                            "repair_requirement": "减少口头语",
                        }
                    ],
                }
            ),
        ]
    )
    result = await model(gateway).preview(content=content(), user_input="有点累", history=())
    assert result["text"] == text and result["passed"] and result["style_advisory"]
    assert result["model_calls"] == 2 and not result["repair_attempted"]
    assert result["checks"][0]["adopted"]
    gateway.assert_exhausted()


async def test_preview_never_returns_an_answer_rejected_by_safety_review():
    issue = {
        "dimension": "fidelity",
        "severity": "blocking",
        "code": "unsafe_medical_advice",
        "explanation": "危险用药建议",
        "source_excerpt": "",
        "output_excerpt": "停药",
        "repair_requirement": "不要提供停药建议",
    }
    gateway = ScriptedModelGateway(
        [
            response({"text": "停药"}),
            response(
                {
                    **PASS,
                    "fidelity_passed": False,
                    "issues": [issue],
                }
            ),
        ]
    )
    result = await model(gateway).preview(content=content(), user_input="该怎么减肥", history=())
    assert not result["passed"] and result["text"] == ""
    assert result["failure_code"]


async def test_timed_out_check_cannot_return_a_pass():
    import asyncio

    class SlowGateway:
        async def complete(self, request):
            await asyncio.Event().wait()

    gateway = SlowGateway()
    with pytest.raises(StudioModelError, match="超时"):
        await model(gateway, request_seconds=0.01).check(content=content())
