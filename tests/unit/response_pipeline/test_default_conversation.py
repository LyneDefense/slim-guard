"""Real Harness/persistence: the doctor owns dialogue, not just an optional add-on."""

import json
from datetime import UTC, datetime

import pytest
from test_group_style_repair import reply, styled

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelPurpose
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.group_chat.failures import REPLY_UNAVAILABLE
from slim_guard.mobile.chat_history import load_chat_history
from slim_guard.orchestration.repository import OrchestrationRepository
from tests.conversation_script import MutationReviewScript

CHECK = {"fidelity_passed": True, "role_passed": True, "expression_passed": True, "issues": []}


@pytest.fixture
async def chat(tmp_path):
    now = datetime(2026, 11, 3, tzinfo=UTC)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'doctor-default.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add(SlimGuardUser(id="user", first_seen_at=now, last_seen_at=now))

    async def run(responses, user_input="你好"):
        model = ScriptedModelGateway(responses)
        runtime = build_agent_runtime(
            database=db,
            model=MutationReviewScript(model),
            definition=AgentRuntimeDefinition(
                models=ModelRuntimeConfig(
                    provider="test", text_model="text", vision_model="vision"
                ),
                code_revision="doctor-default-test",
            ),
            clock=lambda: now,
        )
        result = await runtime.run_user_message(
            AgentRuntimeRequest(user_id="user", text=user_input)
        )
        model.assert_exhausted()
        return result, model

    try:
        yield db, run
    finally:
        await db.close()


@pytest.mark.parametrize("greeting", ["你好。", "嗨，今天怎么样？", "今天有点累。"])
async def test_doctor_only_response_survives_style_persistence_and_history(chat, greeting):
    db, run = chat
    result, model = await run(
        [
            reply(greeting),
            reply({"messages": [{"participant": "coach", "text": greeting}]}),
            styled(greeting),
            reply(CHECK),
            reply({"verdict": "pass"}),
        ]
    )
    assert [(m.participant.value, m.text) for m in result.messages] == [("coach", greeting)]
    assert all(m.notice_text is None for m in result.messages)
    assert result.final_text == greeting
    assert [r.purpose for r in model.requests].count(ModelPurpose.CONVERSATION) == 1
    payload = json.loads(
        next(r for r in model.requests if r.purpose is ModelPurpose.CONVERSATION)
        .messages[-1]
        .content
    )
    assert payload["neutral_draft"] == greeting
    assert "system_candidate" not in payload
    assert payload["output_schema"]["properties"]["schema_version"]["const"] == "3"
    history = await load_chat_history(db, "user", limit=20)
    assert [(m.participant, m.text) for m in history.items if m.role == "assistant"] == [
        ("coach", greeting)
    ]
    invocations = await OrchestrationRepository(db).list_turn_invocations(result.turn_id)
    assert {i.agent_role for i in invocations} == {
        "core",
        "conversation",
        "response_style",
        "response_reviewer",
    }


@pytest.mark.parametrize("failure", ["planning", "review", "content_repair"])
async def test_failed_chat_generation_never_relabels_the_unreviewed_draft_as_system(chat, failure):
    db, run = chat
    source = "这是不能作为系统提示发送的聊天原稿。"
    responses = [reply(source)]
    if failure == "planning":
        responses += [reply("invalid"), reply("invalid again")]
    else:
        responses += [
            reply({"messages": [{"participant": "coach", "text": source}]}),
            styled(source),
            reply(CHECK),
        ]
        if failure == "review":
            responses.append(
                reply(
                    {"verdict": "reject", "issue_type": "style_drift", "reason_summary": "不合适。"}
                )
            )
        else:
            responses += [
                reply(
                    {
                        "verdict": "repair",
                        "repair_target": "core",
                        "issue_type": "missing_user_evidence",
                        "reason_summary": "缺少依据。",
                    }
                ),
                reply("invalid"),
                reply("still invalid"),
            ]
    result, _ = await run(responses)
    assert [(m.participant.value, m.text) for m in result.messages] == [
        ("system_assistant", REPLY_UNAVAILABLE)
    ]
    assert source not in result.final_text
    history = await load_chat_history(db, "user", limit=20)
    assert all(m.text != source for m in history.items if m.role == "assistant")
