from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
)
from slim_guard.agents.contracts import ProfessionalAssessment, TurnDirective
from slim_guard.agents.style import NeutralRenderer, StyleContext
from slim_guard.db.models import (
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.orchestration.coordinator import AgentWorkflowCoordinator, ShadowWorkflowRequest
from slim_guard.orchestration.graph import GraphLoopBudget
from slim_guard.orchestration.repository import OrchestrationRepository

NOW = datetime(2026, 9, 8, tzinfo=UTC)


class ReviewingGateway:
    def __init__(
        self, issue: str | None = None, *, repeat: bool = False, reject: bool = False
    ) -> None:
        self.issue = issue
        self.repeat = repeat
        self.reject = reject
        self.requests: list[ModelRequest] = []
        self.reviews = 0
        self.nutrition_calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.purpose is ModelPurpose.ORCHESTRATOR:
            if len(self.requests) > 1:
                result = TurnDirective(
                    response_path="direct",
                    interaction_kind="question",
                    user_need_summary="缺少资料",
                    response_brief="能补充连续几天的记录吗？",
                    voice_act="ask",
                ).model_dump(mode="json")
            else:
                context = json.loads((request.messages[-1].content or "").split("\n", 1)[1])
                result = TurnDirective(
                    response_path="professional_assessment",
                    interaction_kind="question",
                    user_need_summary="查看变化",
                    response_brief="核对变化",
                    evidence_refs=(context["evidence_catalog"][0]["evidence_id"],),
                    professional_question="能否判断变化？",
                    voice_act="explain",
                ).model_dump(mode="json")
        elif request.purpose is ModelPurpose.NUTRITION:
            self.nutrition_calls += 1
            result = ProfessionalAssessment(
                assessment_type="general",
                overall="单次记录不足以判断趋势。"
                if self.nutrition_calls == 1
                else "需要连续记录后再核对变化。",
            ).model_dump(mode="json")
        elif request.purpose is ModelPurpose.RESPONSE_STYLE:
            context = json.loads(request.messages[-1].content or "{}")
            result = (
                NeutralRenderer()
                .render(
                    StyleContext.model_validate(
                        context.get("style_context", context),
                    )
                )
                .model_dump(mode="json")
            )
        else:
            assert request.purpose is ModelPurpose.RESPONSE_REVIEWER
            self.reviews += 1
            if self.reject:
                result = {
                    "verdict": "reject",
                    "issue_type": "unsupported_claim",
                    "reason_summary": "无法验证结论",
                }
            elif self.issue and (self.reviews == 1 or self.repeat):
                target = {
                    "style_drift": "response_style",
                    "unsupported_professional_claim": "nutrition_expert",
                    "missing_user_evidence": "orchestrator",
                }[self.issue]
                result = {
                    "verdict": "repair",
                    "repair_target": target,
                    "issue_type": self.issue,
                    "reason_summary": "需要核对",
                }
            else:
                result = {"verdict": "pass"}
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                content=json.dumps(result, ensure_ascii=False),
            )
        )

    async def close(self) -> None:
        pass


async def run_workflow(gateway: ReviewingGateway, *, budget: GraphLoopBudget | None = None):
    coordinator = AgentWorkflowCoordinator(
        model=gateway,
        recorder=NullHarnessRunRecorder(),
        model_name="test",
        graph_version="typed-supervisor-v1",
        nutrition_enabled=True,
        reviewer_enabled=True,
        loop_budget=budget,
        clock=lambda: NOW,
    )
    return await coordinator.run_shadow(
        ShadowWorkflowRequest(
            trace_id="trace",
            turn_id="turn",
            user_request="看看变化",
            context=(ModelMessage(role=MessageRole.USER, content="看看变化"),),
            current_items=(
                {"id": "input", "item_type": "user_message", "payload": {"text": "看看变化"}},
            ),
            legacy_response="原有回复",
            deadline_at=NOW + timedelta(seconds=30),
        )
    )


@pytest.mark.parametrize(
    "issue,target",
    [
        ("style_drift", "style_running"),
        ("unsupported_professional_claim", "expert_running"),
        ("missing_user_evidence", "orchestrator_running"),
    ],
)
async def test_review_repairs_only_requested_target_and_reviews_new_candidate(issue, target):
    gateway = ReviewingGateway(issue)
    result = await run_workflow(gateway)
    assert result.status.value == "succeeded", result.failure_code
    assert gateway.reviews == 2
    repairs = [edge for edge in result.transitions if edge.reason.value == "review_repair"]
    assert [edge.target.value for edge in repairs] == [target]
    purposes = [request.purpose for request in gateway.requests]
    assert purposes[-2:] == [ModelPurpose.RESPONSE_STYLE, ModelPurpose.RESPONSE_REVIEWER]
    assert result.legacy_response == "原有回复"
    assert not result.delivered and result.business_write_count == 0
    assert all(not invocation.allowed_tools for invocation in result.invocations)
    verdicts = [item for item in result.artifacts if item.artifact_type == "reviewer_verdict"]
    assert (
        verdicts[0].payload["reviewed_artifact_ids"] != verdicts[1].payload["reviewed_artifact_ids"]
    )
    if issue == "unsupported_professional_claim":
        assert gateway.nutrition_calls == 2
        assert "需要连续记录" in result.shadow_candidate
    elif issue == "missing_user_evidence":
        assert "能补充" in result.shadow_candidate
    else:
        assert gateway.nutrition_calls == 1


async def test_second_repair_request_exhausts_node_budget():
    gateway = ReviewingGateway("style_drift", repeat=True)
    result = await run_workflow(gateway)
    assert result.status.value == "degraded"
    assert result.failure_code == "review_repair_budget_exhausted"
    assert gateway.reviews == 2
    assert result.transitions[-2].reason.value == "budget_exhausted"
    assert len(gateway.requests) == 6


async def test_reject_uses_fact_free_fallback_instead_of_repeating_rejected_assessment():
    gateway = ReviewingGateway(reject=True)
    result = await run_workflow(gateway)
    assert result.status.value == "degraded"
    assert result.failure_code == "review_rejected"
    assert "单次记录" not in result.shadow_candidate
    assert gateway.reviews == 1


async def test_turn_budget_can_disable_all_return_edges():
    gateway = ReviewingGateway("unsupported_professional_claim")
    result = await run_workflow(gateway, budget=GraphLoopBudget(max_upstream_repairs=0))
    assert result.failure_code == "review_repair_budget_exhausted"
    assert gateway.nutrition_calls == 1


async def test_professional_repair_persists_complete_invocations_and_append_only_lineage(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'review.sqlite3'}")
    await database.create_schema()
    try:
        async with database.session() as session, session.begin():
            session.add(SlimGuardUser(id="user", first_seen_at=NOW, last_seen_at=NOW))
            session.add(
                AgentVersionRecord(
                    id="version", manifest_json="{}", code_revision="test", created_at=NOW
                )
            )
            session.add(
                AgentThreadRecord(id="thread", user_id="user", created_at=NOW, last_active_at=NOW)
            )
            session.add(
                AgentTurnRecord(
                    id="turn",
                    thread_id="thread",
                    agent_version_id="version",
                    trigger_type="user_message",
                    status="running",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        repository = OrchestrationRepository(database)
        gateway = ReviewingGateway("unsupported_professional_claim")
        coordinator = AgentWorkflowCoordinator(
            model=gateway,
            recorder=NullHarnessRunRecorder(),
            model_name="test",
            graph_version="test",
            nutrition_enabled=True,
            reviewer_enabled=True,
            persistence=repository,
            clock=lambda: NOW,
        )
        result = await coordinator.run_shadow(
            ShadowWorkflowRequest(
                trace_id="trace",
                turn_id="turn",
                thread_id="thread",
                user_request="看看变化",
                context=(ModelMessage(role=MessageRole.USER, content="看看变化"),),
                current_items=(
                    {"id": "input", "item_type": "user_message", "payload": {"text": "看看变化"}},
                ),
            )
        )
        assert result.status.value == "succeeded", result.failure_code
        invocations = await repository.list_turn_invocations("turn")
        assert len(invocations) == 7
        assert all(item.status == "succeeded" for item in invocations)
        latest = result.artifacts[-1]
        lineage = await repository.lineage(latest.artifact_id)
        assert sum(item.artifact_type == "professional_assessment" for item in lineage) == 2
    finally:
        await database.close()
