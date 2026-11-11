"""Real Harness, SQLite, usage and history; no expression adapter in these tests."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest, AgentScheduledRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    ModelMessage,
    ModelPurpose,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import AgentArtifactRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.expression_style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleExample,
    StyleProfileSnapshot,
)
from slim_guard.features.health_records.weight.repository import WeightRepository
from slim_guard.group_chat.failures import REPLY_UNAVAILABLE
from slim_guard.mobile.chat_history import load_chat_history
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.response_pipeline.profiles import ResolvedStyleProfile, StyleProfileResolver

NOW = datetime(2026, 11, 3, tzinfo=UTC)
PASS = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}


def reply(value):
    return ModelResponse(
        message=ModelMessage(
            role="assistant",
            content=(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)),
        )
    )


def rejection(text, *, dimension="role", severity="repairable"):
    return {
        **PASS,
        f"{dimension}_passed": False,
        "issues": [
            {
                "dimension": dimension,
                "severity": severity,
                "code": "coach_role_violation",
                "explanation": "医生不能播报保存状态",
                "source_excerpt": "",
                "output_excerpt": text,
                "repair_requirement": "去掉操作状态，只对用户陈述作回应",
            }
        ],
    }


def weight_call():
    return ModelResponse(
        message=ModelMessage(
            role="assistant",
            tool_calls=(
                NormalizedToolCall(
                    id="write-once",
                    name="record_weight",
                    arguments={
                        "value": 77.6,
                        "unit": "kg",
                        "condition": "unspecified",
                        "occurrence": {
                            "precision": "unknown",
                            "timezone": "UTC",
                            "basis": "unknown",
                        },
                        "evidence": {
                            "source_kind": "user_reported",
                            "source_refs": ["current-user-message"],
                        },
                    },
                ),
            ),
        )
    )


@pytest.fixture
async def chat(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'direct.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add(SlimGuardUser(id="user", first_seen_at=NOW, last_seen_at=NOW))

    async def run(responses, text="你好", trigger=None):
        model = ScriptedModelGateway(responses)
        runtime = build_agent_runtime(
            database=db,
            model=model,
            definition=AgentRuntimeDefinition(
                models=ModelRuntimeConfig(
                    provider="test", text_model="text", vision_model="vision"
                ),
                code_revision="direct-coach-test",
            ),
            clock=lambda: NOW,
        )
        result = (
            await runtime.run_scheduled(AgentScheduledRequest(user_id="user", trigger=trigger))
            if trigger
            else await runtime.run_user_message(AgentRuntimeRequest(user_id="user", text=text))
        )
        model.assert_exhausted()
        return result, model

    try:
        yield db, run
    finally:
        await db.close()


@pytest.mark.parametrize(
    "user,answer",
    [
        ("你好", "你好，今天怎么样？"),
        ("我今天喝了50升水", "你确定喝了50升水？"),
        ("我今天走了三千步", "三千步，今天也动起来了。"),
        ("我今天有点累", "怎么了，今天忙什么了？"),
    ],
)
async def test_direct_reply_has_no_neutral_stage_or_system_notice(chat, user, answer):
    db, run = chat
    result, model = await run([reply({}), reply({"text": answer}), reply(PASS)], user)
    assert result.failure_code is None
    assert [(m.participant.value, m.text) for m in result.messages] == [("coach", answer)]
    assert [r.purpose for r in model.requests] == [
        ModelPurpose.HARNESS_TURN,
        ModelPurpose.COACH_RESPONSE,
        ModelPurpose.COACH_REVIEW,
    ]
    assert model.requests[0].output_schema_name == "CoreCompletion"
    payload = json.loads(model.requests[1].messages[-1].content)
    assert payload["evidence"]["context"]["current_user_input"]["text"] == user
    style = json.loads(model.requests[1].messages[1].content)
    assert style["type"] == "style_reference_only"
    assert "style" not in payload
    assert "neutral_draft" not in payload
    assert "participant" not in payload["output_schema"]["properties"]
    assert model.requests[1].tools == ()
    history = await load_chat_history(db, "user", limit=20)
    assert [(m.participant, m.text) for m in history.items if m.role == "assistant"] == [
        ("coach", answer)
    ]
    invocations = await OrchestrationRepository(db).list_turn_invocations(result.turn_id)
    assert {i.agent_role for i in invocations} == {"core", "coach_response", "coach_review"}


async def test_exact_schema_error_is_repaired_and_preserved(chat):
    db, run = chat
    result, model = await run(
        [
            reply({}),
            reply({"text": "你好", "participant": "system_assistant"}),
            reply({"text": "你好"}),
            reply(PASS),
        ]
    )
    assert result.final_text == "你好"
    diagnostic = json.loads(model.requests[2].messages[-1].content.split("\n", 1)[1])
    assert diagnostic["validation_errors"][0]["path"] == ["participant"]
    async with db.session() as session:
        artifacts = (
            await session.scalars(
                select(AgentArtifactRecord).where(AgentArtifactRecord.turn_id == result.turn_id)
            )
        ).all()
    candidate = next(a for a in artifacts if a.artifact_type == "coach_reply")
    assert json.loads(candidate.payload_json)["validation_errors"][0]["errors"][0]["path"] == [
        "participant"
    ]


@pytest.mark.parametrize(
    "broken", ["not json", {"text": ""}, {"text": "错误", "notice_text": "假的系统状态"}]
)
async def test_invalid_generation_never_leaks_unreviewed_text(chat, broken):
    _, run = chat
    result, _ = await run([reply({}), reply(broken), reply(broken)])
    assert result.failure_code == "structured_output_invalid"
    assert [(m.participant.value, m.text) for m in result.messages] == [
        ("system_assistant", REPLY_UNAVAILABLE)
    ]


@pytest.mark.parametrize("repair_passes", [True, False])
async def test_semantic_repair_keeps_tool_execution_once(chat, repair_passes):
    db, run = chat
    bad, good = "体重已保存。", "这次测量是77.6公斤，接着看趋势。"
    script = [
        weight_call(),
        reply({}),
        reply({"text": bad}),
        reply(rejection(bad)),
        reply({"text": good}),
        reply(PASS if repair_passes else rejection(good)),
    ]
    result, model = await run(script, "我今天77.6kg")
    assert len([r for r in model.requests if r.purpose is ModelPurpose.HARNESS_TURN]) == 2
    assert "repair_requirement" in model.requests[4].messages[-1].content
    records = (await WeightRepository(db).recent_trend("user")).records
    assert len(records) == 1
    assert len([m for m in result.messages if m.kind.value == "card"]) == 1
    assert any(m.notice_text == "健康记录已更新。" for m in result.messages)
    assert all(m.text != bad for m in result.messages)
    assert bool(result.failure_code) is not repair_passes
    assert any(m.participant.value == "coach" for m in result.messages) is repair_passes


@pytest.mark.parametrize(
    "invalid_completion",
    [
        "你好！有什么可以帮你的吗？",
        '餐食已记录。\n\n{"operation_question":null,"failed_operation_refs":[]}',
        '以下是交接示例：\n{"operation_question":null,"failed_operation_refs":[]}',
        '{"operation_question":null,"failed_operation_refs":[]}\n'
        '{"operation_question":{"text":"要改哪条？"},"failed_operation_refs":[]}',
    ],
)
async def test_core_completion_repair_does_not_replay_the_write(chat, invalid_completion):
    db, run = chat
    result, model = await run(
        [
            weight_call(),
            reply(invalid_completion),
            reply({}),
            reply({"text": "先看趋势。"}),
            reply(PASS),
        ],
        "我今天77.6kg",
    )
    assert len((await WeightRepository(db).recent_trend("user")).records) == 1
    assert "validation_errors" in model.requests[2].messages[-1].content
    assert result.failure_code is None


async def test_business_question_and_doctor_conversation_are_independent(chat):
    _, run = chat
    result, _ = await run(
        [
            reply({"operation_question": {"text": "要修改哪一条记录？"}}),
            reply({"text": "说说今天的情况。"}),
            reply(PASS),
        ],
        "帮我改一下",
    )
    assert result.messages[0].participant.value == "coach"
    notice = result.messages[1]
    assert notice.requires_response is True
    assert notice.notice_text == "要修改哪一条记录？"


async def test_invalid_reviewer_quotes_fail_closed(chat):
    _, run = chat
    bad_review = rejection("并不存在的输出", severity="blocking")
    result, _ = await run(
        [
            reply({}),
            reply({"text": "你好"}),
            reply(bad_review),
            reply(bad_review),
        ]
    )
    assert result.failure_code == "structured_output_invalid"
    assert all(m.participant.value != "coach" for m in result.messages)


async def test_foreign_reply_reference_is_repaired_with_exact_field_feedback(chat):
    _, run = chat
    result, model = await run(
        [
            reply({}),
            reply({"text": "你好", "source_refs": ["foreign-user"]}),
            reply({"text": "你好"}),
            reply(PASS),
        ]
    )
    assert result.failure_code is None
    assert "unavailable_reference" in model.requests[2].messages[-1].content
    assert '"source_refs"' in model.requests[2].messages[-1].content


async def test_invalid_reviewer_quote_repairs_reviewer_not_doctor(chat):
    _, run = chat
    result, model = await run(
        [reply({}), reply({"text": "你好"}), reply(rejection("不存在的引文")), reply(PASS)]
    )
    assert result.failure_code is None
    assert "quote_not_in_reply" in model.requests[3].messages[-1].content
    assert len([r for r in model.requests if r.purpose is ModelPurpose.COACH_RESPONSE]) == 1


async def test_scheduled_turn_has_trigger_without_a_fake_user_message(chat):
    _, run = chat
    result, model = await run(
        [reply({}), reply({"text": "今天还没称重，有空测一下。"}), reply(PASS)],
        trigger="weight_reminder",
    )
    context = json.loads(model.requests[1].messages[-1].content)["evidence"]["context"]
    assert context["turn_trigger"] == "weight_reminder"
    assert context["current_user_input"]["text"] == ""
    assert result.messages[0].participant.value == "coach"


async def test_published_style_is_frozen_before_first_generation_and_reused_for_repair(
    chat, monkeypatch
):
    _, run = chat
    calls = []
    profile = SLIMGUARD_DEFAULT_V1.model_copy(update={"version": "published-test"})
    snapshot = StyleProfileSnapshot(
        profile=profile,
        compiled_prompt="直接、简短，不夸张，不鸡汤。",
        package_hash="synthetic-package",
        examples=(
            StyleExample(
                example_id="fixed-1", style_profile_version=profile.version, text="你确定？"
            ),
        ),
    )

    async def resolve(self):
        calls.append(True)
        return ResolvedStyleProfile(snapshot, profile.version, "runtime_active")

    monkeypatch.setattr(StyleProfileResolver, "resolve", resolve)
    result, model = await run(
        [
            reply({}),
            reply({"text": "已经保存。"}),
            reply(rejection("已经保存。")),
            reply({"text": "你好。"}),
            reply(PASS),
        ]
    )
    assert calls == [True]
    assert result.messages[0].style_profile_version == profile.version
    for request in model.requests:
        if request.purpose is ModelPurpose.COACH_RESPONSE:
            style = json.loads(request.messages[1].content)
            assert style["version"] == snapshot.profile.version
            assert style["package_hash"] == snapshot.package_hash
            assert style["dialogue_examples"][0]["example_id"] == "fixed-1"
            assert "compiled_prompt" not in style


async def test_operation_question_binds_current_message_alias(chat):
    _, run = chat
    result, _ = await run(
        [
            reply(
                {
                    "operation_question": {
                        "text": "要改哪条记录？",
                        "source_refs": ["current-user-message"],
                    }
                }
            ),
            reply({"text": "说说具体情况。"}),
            reply(PASS),
        ],
        "帮我改记录",
    )
    notice = next(m for m in result.messages if m.requires_response)
    assert notice.source_refs and notice.source_refs != ("current-user-message",)
    assert result.failure_code is None


async def test_core_format_failure_after_write_keeps_receipt_without_reexecution(chat):
    db, run = chat
    result, _ = await run([weight_call(), reply("invalid"), reply("invalid again")], "我今天77.6kg")
    assert result.failure_code == "core_completion_invalid"
    assert len((await WeightRepository(db).recent_trend("user")).records) == 1
    assert any(m.kind.value == "card" for m in result.messages)
    assert any(m.notice_text == "健康记录已更新。" for m in result.messages)


async def test_expression_only_rejection_preserves_doctor_and_adoption_trace(chat):
    db, run = chat
    text = "有依据的回答，只是语气还不够简洁。"
    result, model = await run(
        [
            reply({}),
            reply({"text": text}),
            reply(rejection(text, dimension="expression")),
        ]
    )
    assert result.failure_code is None
    assert [(m.participant.value, m.text) for m in result.messages] == [("coach", text)]
    assert [request.purpose for request in model.requests] == [
        ModelPurpose.HARNESS_TURN,
        ModelPurpose.COACH_RESPONSE,
        ModelPurpose.COACH_REVIEW,
    ]
    async with db.session() as session:
        final = await session.scalar(
            select(AgentArtifactRecord).where(
                AgentArtifactRecord.turn_id == result.turn_id,
                AgentArtifactRecord.artifact_type == "outgoing_messages",
            )
        )
        payload = json.loads(final.payload_json)
        assert payload["style_advisory"] is True and payload["adopted_attempt"] == 1


async def test_safety_repair_adopts_second_reviewed_reply_with_style_advisory(chat):
    db, run = chat
    unsafe, safe = "虚构的危险回答。", "可以休息一天。"
    result, model = await run(
        [
            reply({}),
            reply({"text": unsafe}),
            reply(rejection(unsafe, dimension="fidelity")),
            reply({"text": safe}),
            reply(rejection(safe, dimension="expression")),
        ]
    )
    assert result.failure_code is None and result.messages[0].text == safe
    assert len(model.requests) == 5
    async with db.session() as session:
        artifacts = list(
            await session.scalars(
                select(AgentArtifactRecord).where(
                    AgentArtifactRecord.turn_id == result.turn_id,
                )
            )
        )
        final = next(a for a in artifacts if a.artifact_type == "outgoing_messages")
        assert json.loads(final.payload_json)["adopted_attempt"] == 2
        assert json.loads(final.payload_json)["style_advisory"] is True
        adopted_parents = json.loads(final.parent_artifact_ids_json)
        safe_artifact = next(
            a
            for a in artifacts
            if a.artifact_type == "coach_reply"
            and json.loads(a.payload_json)["output"]["text"] == safe
        )
        assert safe_artifact.id in adopted_parents
