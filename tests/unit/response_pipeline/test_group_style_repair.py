"""Real runtime and SQLite, scripted model: bounded coach repair keeps tool results."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.features.health_records.weight.repository import WeightRepository
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.runtime.contracts import AgentRole
from slim_guard.tools.contracts import ToolExecutionMode
from slim_guard.usage.repository import UsageRepository


def reply(value: str | dict[str, object]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
        ),
        usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        finish_reason="stop",
    )


def styled(text: str) -> ModelResponse:
    return reply(
        {
            "text": text,
            "used_block_ids": ["core-neutral-draft"],
            "style_profile_version": "doctor_builtin_v1",
        }
    )


class ClaimAwareScript(ScriptedModelGateway):
    """Keep receipt verification independent of the scripted style-repair steps."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.output_schema_name != "MutationClaims":
            return await super().complete(request)
        self.requests.append(request)
        payload = json.loads(request.messages[-1].content or "{}")
        quote = "已记录今天体重77.6kg。"
        claims = []
        if quote in payload["candidate_text"]:
            claims.append(
                {
                    "quote": quote,
                    "mutation_kind": "health_record",
                    "status": "succeeded",
                    "operation_ids": [payload["receipts"][0]["operation_id"]],
                }
            )
        return reply({"claims": claims})


@pytest.mark.parametrize("repair_target", ["response_style", "core"])
@pytest.mark.parametrize("second_verdict", ["pass", "reject", "repair", "invalid"])
async def test_coach_style_repair_is_bounded_and_preserves_record(
    tmp_path: Path,
    second_verdict: str,
    repair_target: str,
) -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'repair.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    feedback = {
        "verdict": "repair",
        "repair_target": "response_style",
        "issue_type": "style_drift",
        "reason_summary": "请去掉夸张的感叹号，保持简短鼓励。",
        "issues": [
            {"type": "style_drift", "excerpt": "加油！", "explanation": "表达过于用力。"},
        ],
    }
    if repair_target == "core":
        feedback.update(
            {
                "repair_target": "core",
                "issue_type": "missing_user_evidence",
                "reason_summary": "请核对测量条件，不要推断。",
                "issues": [{"type": "missing_user_evidence", "explanation": "测量条件未提供。"}],
            }
        )
    check = {"fidelity_passed": True, "expression_passed": True, "issues": []}
    responses = [
        ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
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
            ),
            usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        ),
        reply("已记录今天体重77.6kg。"),
        reply(
            {
                "schema_version": "2",
                "messages": [
                    {"participant": "coach", "text": "加油。", "source_refs": []},
                    {
                        "participant": "system_assistant",
                        "text": "已记录今天体重77.6kg。",
                        "requires_response": False,
                        "source_refs": [],
                    },
                ],
            }
        ),
        styled("加油！"),
        reply(check),
        reply(feedback),
    ]
    if repair_target == "core":
        responses.extend(
            [
                reply({"neutral_draft": "已记录今天体重77.6kg。"}),
                reply(
                    {
                        "schema_version": "2",
                        "messages": [
                            {"participant": "coach", "text": "加油。", "source_refs": []},
                            {
                                "participant": "system_assistant",
                                "text": "已记录今天体重77.6kg。",
                                "requires_response": False,
                                "source_refs": [],
                            },
                        ],
                    }
                ),
            ]
        )
    responses.extend([styled("加油。"), reply(check)])
    if second_verdict == "invalid":
        responses.extend([reply("not JSON"), reply("still not JSON")])
    elif second_verdict == "repair":
        responses.append(reply(feedback))
    elif second_verdict == "reject":
        responses.append(
            reply(
                {
                    "verdict": "reject",
                    "issue_type": "style_drift",
                    "reason_summary": "仍不合适。",
                }
            )
        )
    else:
        responses.append(reply({"verdict": "pass"}))
    model = ClaimAwareScript(responses)
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="group-style-repair",
        ),
        clock=lambda: now,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="今天体重77.6kg",
                source_message_id="weight-message-1",
                occurred_at=now,
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        coach_messages = [m for m in result.messages if m.participant.value == "coach"]
        assert [m.text for m in coach_messages] == (["加油。"] if second_verdict == "pass" else [])
        system = [m for m in result.messages if m.participant.value == "system_assistant"]
        assert len(system) == 2
        assert system[0].text == "已记录今天体重77.6kg。"
        assert system[1].card is not None
        assert system[1].card.source_execution_id == "write-once"
        assert len((await WeightRepository(database).recent_trend("user-1")).records) == 1

        repository = OrchestrationRepository(database)
        invocations = await repository.list_turn_invocations(result.turn_id)
        artifacts = await repository.list_artifacts(result.turn_id)
        styles = sorted(
            (i for i in invocations if i.agent_role == AgentRole.RESPONSE_STYLE.value),
            key=lambda i: i.attempt,
        )
        reviews = sorted(
            (i for i in invocations if i.agent_role == AgentRole.RESPONSE_REVIEWER.value),
            key=lambda i: i.attempt,
        )
        assert [i.attempt for i in styles] == [1, 2]
        assert [i.attempt for i in reviews] == [1, 2]
        if repair_target == "response_style":
            assert styles[1].parent_invocation_id == reviews[0].invocation_id
            assert set(styles[0].input_artifact_ids).issubset(styles[1].input_artifact_ids)
        else:
            core = next(i for i in invocations if i.agent_role == "core" and i.attempt == 2)
            conversation = next(
                i for i in invocations if i.agent_role == "conversation" and i.attempt == 2
            )
            assert core.parent_invocation_id == reviews[0].invocation_id
            assert conversation.parent_invocation_id == core.invocation_id
            assert styles[1].parent_invocation_id == conversation.invocation_id
            assert core.allowed_tools == ()
        assert reviews[1].parent_invocation_id == styles[1].invocation_id
        assert styles[1].deadline_at == styles[0].deadline_at
        assert styles[0].output_artifact_id in styles[1].input_artifact_ids
        assert reviews[0].output_artifact_id in styles[1].input_artifact_ids
        assert sum(a.artifact_type == "style_resolution" for a in artifacts) == 1
        # Mutation verification is a response-processing service, not another
        # agent invocation. Its calls still belong in the billed Turn usage.
        agent_calls = [r for r in model.requests if r.output_schema_name != "MutationClaims"]
        assert sum(i.total_token_count for i in invocations) == len(agent_calls) * 5
        usage = await UsageRepository(database).detail(
            user_id="user-1", request_id=result.turn_id, limit=100, offset=0
        )
        assert usage["summary"]["calls"] == len(model.requests)
        assert usage["summary"]["total_tokens"] == len(model.requests) * 5
        rewrites = [r for r in model.requests if r.output_schema_name == "StyledResponse"]
        assert len(rewrites) == 2
        first_payload = json.loads(rewrites[0].messages[-1].content)
        repaired_payload = json.loads(rewrites[1].messages[-1].content)
        assert first_payload["style_context"] == repaired_payload["style_context"]
        if repair_target == "response_style":
            assert (
                repaired_payload["review_feedback"]["reason_summary"] == feedback["reason_summary"]
            )
            assert repaired_payload["review_feedback"]["issues"] == feedback["issues"]
        else:
            core_request = next(
                r for r in model.requests if r.output_schema_name == "CoreRepairDraft"
            )
            core_payload = json.loads(core_request.messages[-1].content)
            assert core_payload["repair_instruction"] == feedback["reason_summary"]
            assert core_request.tools == ()
        assert sum(r.purpose is ModelPurpose.HARNESS_TURN for r in model.requests) == (
            3 if repair_target == "core" else 2
        )
        assert sum(r.purpose is ModelPurpose.CONVERSATION for r in model.requests) == (
            2 if repair_target == "core" else 1
        )
        model.assert_exhausted()
    finally:
        await database.close()
