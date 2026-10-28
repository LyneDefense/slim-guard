from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.agents.nutrition import CONSULT_NUTRITION_TOOL_NAME
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.runtime.contracts import AgentRole
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


async def prepare_database(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'response-finalization.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    return database


def response(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


def nutrition_tool_call() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id="nutrition-consult-1",
                    name=CONSULT_NUTRITION_TOOL_NAME,
                    arguments={"professional_question": "减脂期间晚餐怎么搭配？"},
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


async def test_system_response_does_not_enter_style_or_review(tmp_path) -> None:
    database = await prepare_database(tmp_path)
    model = ScriptedModelGateway(
        (
            response("已收到。"),
            conversation("已收到。", coach=None),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="single-path-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(AgentRuntimeRequest(user_id="user-1", text="你好"))
        assert result.final_text == "已收到。"
        assert [r.purpose for r in model.requests] == [
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.CONVERSATION,
        ]
        model.assert_exhausted()
    finally:
        await database.close()


def conversation(system: str | None, *, coach: str | None) -> ModelResponse:
    messages = []
    if coach is not None:
        messages.append(
            {
                "participant": "coach",
                "text": coach,
                "source_refs": [],
            }
        )
    messages.append(
        {
            "participant": "system_assistant",
            "text": system or "已收到。",
            "requires_response": False,
            "source_refs": [],
        }
    )
    return response(
        json.dumps(
            {
                "schema_version": "2",
                "messages": messages,
            },
            ensure_ascii=False,
        )
    )


async def test_group_chat_routes_coach_text_through_style_and_keeps_system_text(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    styled = {
        "text": "行，这顿搭配挺好。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    model = ScriptedModelGateway(
        (
            response("今天这条晚餐处理好了。"),
            response(
                json.dumps(
                    {
                        "schema_version": "2",
                        "messages": [
                            {
                                "participant": "coach",
                                "text": "这顿搭配不错。",
                                "source_refs": [],
                            },
                            {
                                "participant": "system_assistant",
                                "text": "今天这条晚餐处理好了。",
                                "requires_response": False,
                                "source_refs": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                )
            ),
            response(json.dumps(styled, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="group-chat-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="这是我的晚餐。",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        assert result.messages[0].participant.value == "coach"
        assert result.messages[0].text == styled["text"]
        assert result.messages[1].participant.value == "system_assistant"
        assert result.messages[1].text == "今天这条晚餐处理好了。"
        assert result.final_text == "行，这顿搭配挺好。\n今天这条晚餐处理好了。"
        assert [request.purpose.value for request in model.requests] == [
            "harness_turn",
            "conversation",
            "response_style",
            "response_style",
            "response_reviewer",
        ]
        conversation_payload = json.loads(model.requests[1].messages[-1].content or "{}")
        assert conversation_payload["conversation_context"]["current_user_input"] == {
            "text": "这是我的晚餐。",
            "attachments": [],
        }
        repository = OrchestrationRepository(database)
        invocations = await repository.list_turn_invocations(result.turn_id)
        artifacts = await repository.list_artifacts(result.turn_id)
        resolutions = [item for item in artifacts if item.artifact_type == "style_resolution"]
        assert len(resolutions) == 1
        plans = [item for item in artifacts if item.artifact_type == "response_plan"]
        assert len(plans) == 1
        assert resolutions[0].parent_artifact_ids == (plans[0].artifact_id,)
        coach_neutral = next(
            item for item in artifacts if item.artifact_type == "coach_neutral_response"
        )
        assert coach_neutral.payload["text"] == "这顿搭配不错。"
        conversation = next(
            item for item in invocations if item.agent_role == AgentRole.CONVERSATION.value
        )
        style = next(
            item for item in invocations if item.agent_role == AgentRole.RESPONSE_STYLE.value
        )
        review = next(
            item for item in invocations if item.agent_role == AgentRole.RESPONSE_REVIEWER.value
        )
        assert style.parent_invocation_id == conversation.invocation_id
        assert review.parent_invocation_id == style.invocation_id
        assert {resolutions[0].artifact_id, coach_neutral.artifact_id}.issubset(
            style.input_artifact_ids
        )
        model.assert_exhausted()
    finally:
        await database.close()


async def test_multiple_coach_messages_keep_model_order_and_are_reviewed_individually(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    coach_texts = ("这顿搭配不错。", "继续保持这个节奏。")
    styled_texts = ("这顿搭配不错。", "继续保持这个节奏。")
    model_responses = [
        response("收到。"),
        response(
            json.dumps(
                {
                    "schema_version": "2",
                    "messages": [
                        {
                            "participant": "coach",
                            "text": coach_texts[0],
                            "source_refs": [],
                        },
                        {
                            "participant": "system_assistant",
                            "text": "收到。",
                            "requires_response": False,
                            "source_refs": [],
                        },
                        {
                            "participant": "coach",
                            "text": coach_texts[1],
                            "source_refs": [],
                        },
                    ],
                },
                ensure_ascii=False,
            )
        ),
    ]
    for text in styled_texts:
        styled = {
            "text": text,
            "used_block_ids": ["core-neutral-draft"],
            "used_claim_ids": [],
            "used_action_ids": [],
            "preserved_risk_flags": [],
            "preserved_citation_refs": [],
            "style_profile_version": "doctor_builtin_v1",
        }
        model_responses.extend(
            [
                response(json.dumps(styled, ensure_ascii=False)),
                response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
                response('{"verdict":"pass"}'),
            ]
        )
    model = ScriptedModelGateway(tuple(model_responses))
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="multiple-coach-message-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(user_id="user-1", text="今天吃得不错。")
        )
        assert [message.participant.value for message in result.messages] == [
            "coach",
            "system_assistant",
            "coach",
        ]
        assert [message.text for message in result.messages] == [
            *styled_texts[:1],
            "收到。",
            styled_texts[1],
        ]
        assert result.final_text == "\n".join((styled_texts[0], "收到。", styled_texts[1]))
        assert (
            sum(request.purpose is ModelPurpose.RESPONSE_REVIEWER for request in model.requests)
            == 2
        )
        model.assert_exhausted()
    finally:
        await database.close()


async def test_professional_response_runs_nutrition_style_and_reviewer_as_children(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    assessment = {
        "assessment_type": "general",
        "overall": "当前资料不足以给出具体搭配结论。",
        "uncertainty_note": "需要知道实际食物和份量。",
    }
    styled = {
        "text": "目前信息还不够。把晚餐的食物和大概份量告诉我，我再帮你看。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    model = ScriptedModelGateway(
        (
            nutrition_tool_call(),
            response(json.dumps(assessment, ensure_ascii=False)),
            response("目前信息还不够，请告诉我晚餐具体吃什么和大概份量。"),
            conversation(None, coach=str(styled["text"])),
            response(json.dumps(styled, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="professional-finalization-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="减脂期间晚餐怎么搭配？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        repository = OrchestrationRepository(database)
        invocations = await repository.list_turn_invocations(result.turn_id)
        artifacts = await repository.list_artifacts(result.turn_id)
        by_role = {item.agent_role: item for item in invocations}

        assert result.messages[0].text == styled["text"]
        assert [request.purpose for request in model.requests] == [
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.NUTRITION,
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.CONVERSATION,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_REVIEWER,
        ]
        assert set(by_role) == {
            AgentRole.CORE.value,
            AgentRole.CONVERSATION.value,
            AgentRole.NUTRITION_EXPERT.value,
            AgentRole.RESPONSE_STYLE.value,
            AgentRole.RESPONSE_REVIEWER.value,
        }
        core = by_role[AgentRole.CORE.value]
        nutrition = by_role[AgentRole.NUTRITION_EXPERT.value]
        style = by_role[AgentRole.RESPONSE_STYLE.value]
        reviewer = by_role[AgentRole.RESPONSE_REVIEWER.value]
        assert nutrition.parent_invocation_id == core.invocation_id
        assert style.parent_invocation_id == by_role[AgentRole.CONVERSATION.value].invocation_id
        assert reviewer.parent_invocation_id == style.invocation_id
        verdict = next(item for item in artifacts if item.artifact_type == "reviewer_verdict")
        assert verdict.payload["verdict"] == "pass"
        review_payload = json.loads(model.requests[-1].messages[-1].content or "{}")
        assert review_payload["supporting_assessment"]["overall"] == assessment["overall"]
        assert review_payload["assessment"] is None
        assert not any(item.agent_role == "orchestrator" for item in invocations)
    finally:
        await database.close()


async def test_reviewer_routes_style_drift_back_to_same_profile_for_one_repair(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    assessment = {
        "assessment_type": "general",
        "overall": "需要先补充晚餐内容。",
        "uncertainty_note": "当前没有食物信息。",
    }
    first_style = {
        "text": "今晚想吃什么都可以。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    repaired_style = {
        **first_style,
        "text": "先把今晚准备吃的食物和大概份量告诉我，我再帮你看。",
    }
    model = ScriptedModelGateway(
        (
            nutrition_tool_call(),
            response(json.dumps(assessment, ensure_ascii=False)),
            response("请先告诉我今晚准备吃什么和大概份量。"),
            conversation(None, coach=str(first_style["text"])),
            response(json.dumps(first_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response(
                json.dumps(
                    {
                        "verdict": "repair",
                        "repair_target": "response_style",
                        "issue_type": "changed_meaning",
                        "reason_summary": "风格转换改变了原计划的询问含义。",
                    },
                    ensure_ascii=False,
                )
            ),
            response(json.dumps(repaired_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="style-repair-finalization-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="减脂期间晚餐怎么搭配？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        repository = OrchestrationRepository(database)
        invocations = await repository.list_turn_invocations(result.turn_id)
        artifacts = await repository.list_artifacts(result.turn_id)
        styles = sorted(
            (item for item in invocations if item.agent_role == AgentRole.RESPONSE_STYLE.value),
            key=lambda item: item.attempt,
        )
        reviewers = sorted(
            (item for item in invocations if item.agent_role == AgentRole.RESPONSE_REVIEWER.value),
            key=lambda item: item.attempt,
        )

        assert result.messages[0].text == repaired_style["text"]
        assert [item.attempt for item in styles] == [1, 2]
        assert styles[0].agent_version == styles[1].agent_version
        assert styles[1].parent_invocation_id == reviewers[0].invocation_id
        assert reviewers[1].parent_invocation_id == styles[1].invocation_id
        styled_attempts = sorted(
            item.payload["attempt"] for item in artifacts if item.artifact_type == "styled_response"
        )
        assert styled_attempts == [1, 2]
        verdicts = sorted(
            (item for item in artifacts if item.artifact_type == "reviewer_verdict"),
            key=lambda item: item.payload["attempt"],
        )
        assert [item.payload["verdict"] for item in verdicts] == ["repair", "pass"]
    finally:
        await database.close()


async def test_reviewer_routes_missing_user_evidence_back_to_core_agent(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    assessment = {
        "assessment_type": "general",
        "overall": "当前资料不足以给出具体搭配结论。",
        "uncertainty_note": "需要知道实际食物和份量。",
    }
    first_style = {
        "text": "晚餐这样搭配就行。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    repaired_style = {
        **first_style,
        "text": "把今晚准备吃的食物和大概份量告诉我，我再帮你判断。",
    }
    model = ScriptedModelGateway(
        (
            nutrition_tool_call(),
            response(json.dumps(assessment, ensure_ascii=False)),
            response("当前内容稿认为晚餐这样搭配就行。"),
            conversation(None, coach=str(first_style["text"])),
            response(json.dumps(first_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response(
                json.dumps(
                    {
                        "verdict": "repair",
                        "repair_target": "core",
                        "issue_type": "missing_user_evidence",
                        "reason_summary": "缺少用户实际晚餐内容和份量。",
                    },
                    ensure_ascii=False,
                )
            ),
            response(
                json.dumps(
                    {"neutral_draft": ("请告诉我今晚准备吃什么和大概份量，我再帮你判断。")},
                    ensure_ascii=False,
                )
            ),
            conversation(None, coach=str(repaired_style["text"])),
            response(json.dumps(repaired_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="core-owner-repair-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="减脂期间晚餐怎么搭配？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        invocations = await OrchestrationRepository(database).list_turn_invocations(result.turn_id)
        cores = sorted(
            (item for item in invocations if item.agent_role == AgentRole.CORE.value),
            key=lambda item: item.attempt,
        )
        styles = sorted(
            (item for item in invocations if item.agent_role == AgentRole.RESPONSE_STYLE.value),
            key=lambda item: item.attempt,
        )
        reviewers = sorted(
            (item for item in invocations if item.agent_role == AgentRole.RESPONSE_REVIEWER.value),
            key=lambda item: item.attempt,
        )

        assert result.messages[0].text == repaired_style["text"]
        assert [item.attempt for item in cores] == [1, 2]
        assert cores[1].parent_invocation_id == reviewers[0].invocation_id
        routed = next(
            i
            for i in invocations
            if i.agent_role == AgentRole.CONVERSATION.value and i.attempt == 2
        )
        assert routed.parent_invocation_id == cores[1].invocation_id
        assert styles[1].parent_invocation_id == routed.invocation_id
        assert reviewers[1].parent_invocation_id == styles[1].invocation_id
    finally:
        await database.close()


async def test_reviewer_routes_professional_issue_to_nutrition_then_core(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    initial_assessment = {
        "assessment_type": "general",
        "overall": "这顿晚餐可以直接照旧吃。",
    }
    repaired_assessment = {
        "assessment_type": "general",
        "overall": "当前资料不足以给出具体搭配结论。",
        "uncertainty_note": "需要知道实际食物和份量。",
    }
    first_style = {
        "text": "照旧吃就可以。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    repaired_style = {
        **first_style,
        "text": "把晚餐的具体食物和大概份量告诉我，我再帮你看。",
    }
    model = ScriptedModelGateway(
        (
            nutrition_tool_call(),
            response(json.dumps(initial_assessment, ensure_ascii=False)),
            response("当前内容稿认为照旧吃就可以。"),
            conversation(None, coach=str(first_style["text"])),
            response(json.dumps(first_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response(
                json.dumps(
                    {
                        "verdict": "repair",
                        "repair_target": "nutrition_expert",
                        "issue_type": "unsupported_professional_claim",
                        "reason_summary": "专业结论缺少当前证据支持。",
                    },
                    ensure_ascii=False,
                )
            ),
            response(json.dumps(repaired_assessment, ensure_ascii=False)),
            response(
                json.dumps(
                    {"neutral_draft": ("请告诉我晚餐的具体食物和大概份量，我再帮你看。")},
                    ensure_ascii=False,
                )
            ),
            conversation(None, coach=str(repaired_style["text"])),
            response(json.dumps(repaired_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(
                provider="zhipu",
                text_model="glm-5.2",
                vision_model="glm-5v-turbo",
            ),
            code_revision="nutrition-owner-repair-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="减脂期间晚餐怎么搭配？",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        invocations = await OrchestrationRepository(database).list_turn_invocations(result.turn_id)
        nutrition = sorted(
            (item for item in invocations if item.agent_role == AgentRole.NUTRITION_EXPERT.value),
            key=lambda item: item.attempt,
        )
        cores = sorted(
            (item for item in invocations if item.agent_role == AgentRole.CORE.value),
            key=lambda item: item.attempt,
        )
        reviewers = sorted(
            (item for item in invocations if item.agent_role == AgentRole.RESPONSE_REVIEWER.value),
            key=lambda item: item.attempt,
        )

        assert result.messages[0].text == repaired_style["text"]
        assert [item.attempt for item in nutrition] == [1, 2]
        assert nutrition[1].parent_invocation_id == reviewers[0].invocation_id
        assert cores[1].parent_invocation_id == nutrition[1].invocation_id
        assert reviewers[1].attempt == 2
        nutrition_requests = [r for r in model.requests if r.purpose is ModelPurpose.NUTRITION]
        repair_payload = json.loads(nutrition_requests[1].messages[-1].content or "{}")
        assert repair_payload["review_feedback"]["reason_summary"] == "专业结论缺少当前证据支持。"
        assert repair_payload["review_feedback"]["repair_target"] == "nutrition_expert"
        review_requests = [r for r in model.requests if r.purpose is ModelPurpose.RESPONSE_REVIEWER]
        last_review = json.loads(review_requests[-1].messages[-1].content or "{}")
        assert last_review["supporting_assessment"]["overall"] == repaired_assessment["overall"]
    finally:
        await database.close()
