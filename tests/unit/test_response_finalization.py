from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.runtime import AgentRuntimeRequest
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


async def test_on_mode_uses_core_plan_then_one_style_path_without_orchestrator(
    tmp_path,
) -> None:
    database = await prepare_database(tmp_path)
    styled = {
        "text": "收到，今天这条我已经记下来了。",
        "used_block_ids": ["core-neutral-draft"],
        "used_claim_ids": [],
        "used_action_ids": [],
        "preserved_risk_flags": [],
        "preserved_citation_refs": [],
        "style_profile_version": "doctor_builtin_v1",
    }
    model = ScriptedModelGateway(
        (
            response("已记录今天的数据。"),
            response(json.dumps(styled, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="core-primary-finalization-test",
            multi_agent_mode="on",
            response_reviewer_enabled=True,
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="今天先这样。",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )

        repository = OrchestrationRepository(database)
        invocations = await repository.list_turn_invocations(result.turn_id)
        artifacts = await repository.list_artifacts(result.turn_id)

        assert result.final_text == styled["text"]
        assert [request.purpose for request in model.requests] == [
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_STYLE,
        ]
        assert {item.agent_role for item in invocations} == {
            AgentRole.CORE.value,
            AgentRole.RESPONSE_STYLE.value,
        }
        core = next(item for item in invocations if item.agent_role == AgentRole.CORE.value)
        style = next(
            item for item in invocations if item.agent_role == AgentRole.RESPONSE_STYLE.value
        )
        assert core.output_schema == "ResponsePlan"
        assert style.parent_invocation_id == core.invocation_id
        assert style.output_schema == "StyledResponse"
        assert not any(item.agent_role == "orchestrator" for item in invocations)
        assert {item.artifact_type for item in artifacts} >= {
            "response_plan",
            "neutral_response",
            "style_resolution",
            "styled_response",
        }
        styled_artifact = next(
            item for item in artifacts if item.artifact_type == "styled_response"
        )
        assert styled_artifact.payload["neutral_text"] == "已记录今天的数据。"
        assert styled_artifact.payload["text"] == styled["text"]
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
            response(json.dumps(styled, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="professional-finalization-test",
            multi_agent_mode="on",
            nutrition_agent_enabled=True,
            response_reviewer_enabled=True,
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

        assert result.final_text == styled["text"]
        assert [request.purpose for request in model.requests] == [
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.NUTRITION,
            ModelPurpose.HARNESS_TURN,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_STYLE,
            ModelPurpose.RESPONSE_REVIEWER,
        ]
        assert set(by_role) == {
            AgentRole.CORE.value,
            AgentRole.NUTRITION_EXPERT.value,
            AgentRole.RESPONSE_STYLE.value,
            AgentRole.RESPONSE_REVIEWER.value,
        }
        core = by_role[AgentRole.CORE.value]
        nutrition = by_role[AgentRole.NUTRITION_EXPERT.value]
        style = by_role[AgentRole.RESPONSE_STYLE.value]
        reviewer = by_role[AgentRole.RESPONSE_REVIEWER.value]
        assert nutrition.parent_invocation_id == core.invocation_id
        assert style.parent_invocation_id == core.invocation_id
        assert reviewer.parent_invocation_id == style.invocation_id
        verdict = next(item for item in artifacts if item.artifact_type == "reviewer_verdict")
        assert verdict.payload["verdict"] == "pass"
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
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="style-repair-finalization-test",
            multi_agent_mode="on",
            nutrition_agent_enabled=True,
            response_reviewer_enabled=True,
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

        assert result.final_text == repaired_style["text"]
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
            response("晚餐这样搭配就行。"),
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
            response(json.dumps(repaired_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="core-owner-repair-test",
            multi_agent_mode="on",
            nutrition_agent_enabled=True,
            response_reviewer_enabled=True,
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

        assert result.final_text == repaired_style["text"]
        assert [item.attempt for item in cores] == [1, 2]
        assert cores[1].parent_invocation_id == reviewers[0].invocation_id
        assert styles[1].parent_invocation_id == cores[1].invocation_id
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
            response("照旧吃就可以。"),
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
            response(json.dumps(repaired_style, ensure_ascii=False)),
            response('{"fidelity_passed":true,"expression_passed":true,"issues":[]}'),
            response('{"verdict":"pass"}'),
        )
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            model_provider="zhipu",
            text_model="glm-5.2",
            vision_model="glm-5v-turbo",
            code_revision="nutrition-owner-repair-test",
            multi_agent_mode="on",
            nutrition_agent_enabled=True,
            response_reviewer_enabled=True,
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

        assert result.final_text == repaired_style["text"]
        assert [item.attempt for item in nutrition] == [1, 2]
        assert nutrition[1].parent_invocation_id == reviewers[0].invocation_id
        assert cores[1].parent_invocation_id == nutrition[1].invocation_id
        assert reviewers[1].attempt == 2
    finally:
        await database.close()
