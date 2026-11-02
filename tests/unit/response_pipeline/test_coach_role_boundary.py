"""Real SQLite/runtime: reject wrong-role coach text without losing a completed write."""

from datetime import UTC, datetime

import pytest
from test_group_style_repair import ClaimAwareScript, reply, styled

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse, NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.features.health_records.weight.repository import WeightRepository
from slim_guard.group_chat import COACH_ROLE_VIOLATION
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.tools.contracts import ToolExecutionMode


@pytest.mark.parametrize("reject_at", ["shared_review", "outer_review"])
async def test_wrong_role_coach_is_suppressed_and_record_receipt_survives(tmp_path, reject_at):
    now = datetime(2026, 11, 2, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'coach-role.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
    system_text = "已记录今天体重77.6kg。"
    source = "体重已保存。"
    model = ClaimAwareScript(
        [
            ModelResponse(
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
            ),
            reply(system_text),
            reply(
                {
                    "schema_version": "2",
                    "messages": [
                        {"participant": "coach", "text": source, "source_refs": []},
                        {
                            "participant": "system_assistant",
                            "text": system_text,
                            "requires_response": False,
                            "source_refs": [],
                        },
                    ],
                }
            ),
            styled(source),
            reply(
                {
                    "fidelity_passed": True,
                    "role_passed": reject_at != "shared_review",
                    "expression_passed": True,
                    "issues": []
                    if reject_at == "outer_review"
                    else [
                        {
                            "dimension": "role",
                            "severity": "blocking",
                            "code": COACH_ROLE_VIOLATION,
                            "explanation": "记录状态不属于医生职责。",
                            "source_excerpt": source,
                            "output_excerpt": source,
                            "repair_requirement": "保留系统说明，取消越界医生候选。",
                        }
                    ],
                }
            ),
            reply(
                {"verdict": "pass"}
                if reject_at == "shared_review"
                else {
                    "verdict": "reject",
                    "issue_type": COACH_ROLE_VIOLATION,
                    "reason_summary": "医生不反馈记录状态。",
                }
            ),
        ]
    )
    runtime = build_agent_runtime(
        database=database,
        model=model,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="coach-role-test",
        ),
        clock=lambda: now,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="今天体重77.6kg",
                source_message_id="weight-1",
                occurred_at=now,
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        assert not [m for m in result.messages if m.participant.value == "coach"]
        system = [m for m in result.messages if m.participant.value == "system_assistant"]
        assert len(system) == 2 and system[0].text == system_text
        assert system[1].card is not None and system[1].card.source_execution_id == "write-once"
        assert len((await WeightRepository(database).recent_trend("user-1")).records) == 1
        if reject_at == "shared_review":
            artifacts = await OrchestrationRepository(database).list_artifacts(result.turn_id)
            assert any(a.payload.get("failure_code") == COACH_ROLE_VIOLATION for a in artifacts)
        model.assert_exhausted()
    finally:
        await database.close()
