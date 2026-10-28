"""Review remains part of response processing without a separate feature switch."""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from slim_guard.agent.assembly import responses as response_assembly
from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelPurpose, ModelResponse
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.runtime.contracts import AgentRole


def reply(value: str | dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
        ),
        finish_reason="stop",
    )


async def test_missing_reviewer_dependency_fails_at_assembly() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    try:
        with (
            patch.object(response_assembly, "ResponseReviewerAgent", return_value=None),
            pytest.raises(ValueError, match="Response Reviewer is required"),
        ):
            build_agent_runtime(
                database=database,
                model=ScriptedModelGateway([]),
                definition=AgentRuntimeDefinition(
                    models=ModelRuntimeConfig(
                        provider="test",
                        text_model="text",
                        vision_model="vision",
                    ),
                    code_revision="missing-reviewer-test",
                ),
            )
    finally:
        await database.close()


@pytest.mark.parametrize("scenario", ["system_only", "review_rejects", "review_invalid"])
async def test_group_chat_only_sends_coach_after_successful_review(tmp_path, scenario: str) -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    coach = None if scenario == "system_only" else "加油。"
    responses = [
        reply("已收到消息。"),
        reply(
            {
                "schema_version": "2",
                "messages": [
                    *(
                        [{"participant": "coach", "text": coach, "source_refs": []}]
                        if coach is not None
                        else []
                    ),
                    {
                        "participant": "system_assistant",
                        "text": "已收到消息。",
                        "requires_response": False,
                        "source_refs": [],
                    },
                ],
            }
        ),
    ]
    if coach is not None:
        responses.extend(
            [
                reply(
                    {
                        "text": coach,
                        "used_block_ids": ["core-neutral-draft"],
                        "used_claim_ids": [],
                        "used_action_ids": [],
                        "preserved_risk_flags": [],
                        "preserved_citation_refs": [],
                        "style_profile_version": "doctor_builtin_v1",
                    }
                ),
                reply({"fidelity_passed": True, "expression_passed": True, "issues": []}),
            ]
        )
        if scenario == "review_rejects":
            responses.append(
                reply(
                    {
                        "verdict": "reject",
                        "issue_type": "style_drift",
                        "reason_summary": "候选不适合当前交流。",
                    }
                )
            )
        else:
            responses.extend([reply("not JSON"), reply("still not JSON")])
    model = ScriptedModelGateway(responses)
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="required-review-test",
        ),
        clock=lambda: now,
    )
    try:
        result = await runtime.run_user_message(AgentRuntimeRequest(user_id="user-1", text="收到"))
        assert result.final_text == "已收到消息。"
        assert [(m.participant.value, m.text) for m in result.messages] == [
            ("system_assistant", "已收到消息。"),
        ]
        invocations = await OrchestrationRepository(database).list_turn_invocations(result.turn_id)
        artifacts = await OrchestrationRepository(database).list_artifacts(result.turn_id)
        assert sum(item.artifact_type == "style_resolution" for item in artifacts) == 1
        reviews = [i for i in invocations if i.agent_role == AgentRole.RESPONSE_REVIEWER.value]
        if coach is None:
            assert reviews == []
            assert not any(item.artifact_type == "coach_neutral_response" for item in artifacts)
            assert [r.purpose for r in model.requests] == [
                ModelPurpose.HARNESS_TURN,
                ModelPurpose.CONVERSATION,
            ]
        else:
            assert len(reviews) == 1
            assert model.requests[-1].purpose is ModelPurpose.RESPONSE_REVIEWER
        model.assert_exhausted()
    finally:
        await database.close()
