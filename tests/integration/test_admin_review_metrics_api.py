from __future__ import annotations

import json
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from slim_guard.agents.contracts import payload_sha256
from slim_guard.config import Settings
from slim_guard.db.models import (
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    InteractionTraceRecord,
    SlimGuardUser,
)
from slim_guard.main import create_app


async def test_workflow_review_metrics_expose_rates_and_denominators(
    test_settings: Settings,
) -> None:
    settings = test_settings.model_copy(
        update={
            "wecom_corp_id": "",
            "wecom_kf_secret": "",
            "wecom_open_kf_id": "",
            "wecom_callback_token": "",
            "wecom_callback_aes_key": "",
            "admin_username": "operator",
            "admin_password": "a-long-test-password",
        }
    )
    app = create_app(settings)
    now = datetime.now(UTC)

    def invocation(
        invocation_id: str,
        *,
        trace_id: str,
        turn_id: str,
        attempt: int,
        output_artifact_id: str,
    ) -> AgentInvocationRecord:
        return AgentInvocationRecord(
            id=invocation_id,
            trace_id=trace_id,
            turn_id=turn_id,
            graph_version="review-v1",
            agent_role="response_reviewer",
            agent_version="reviewer-v1",
            attempt=attempt,
            caller="coordinator",
            input_artifact_ids_json="[]",
            input_schema_version="1",
            allowed_tools_json="[]",
            privacy_scopes_json="[]",
            deadline_at=now,
            max_model_calls=1,
            max_tool_calls=0,
            max_total_tokens=1000,
            input_payload_json="{}",
            status="succeeded",
            output_schema="ReviewerVerdict",
            output_schema_version="1",
            output_artifact_id=output_artifact_id,
            tool_receipt_ids_json="[]",
            started_at=now,
            completed_at=now,
        )

    def artifact(
        artifact_id: str,
        *,
        turn_id: str,
        invocation_id: str,
        payload: dict[str, object],
        parent_id: str,
    ) -> AgentArtifactRecord:
        return AgentArtifactRecord(
            id=artifact_id,
            turn_id=turn_id,
            invocation_id=invocation_id,
            producer_role="response_reviewer",
            artifact_type="reviewer_verdict",
            schema_version="1",
            parent_artifact_ids_json=json.dumps([parent_id]),
            payload_sha256=payload_sha256(payload),
            payload_json=json.dumps(payload),
            created_at=now,
        )

    async with app.router.lifespan_context(app):
        async with app.state.database.session() as session, session.begin():
            session.add(
                SlimGuardUser(
                    id="user-review-metrics",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            session.add(
                AgentVersionRecord(
                    id="review-version",
                    manifest_json="{}",
                    code_revision="test",
                    created_at=now,
                )
            )
            session.add(
                AgentThreadRecord(
                    id="review-thread",
                    user_id="user-review-metrics",
                    created_at=now,
                    last_active_at=now,
                )
            )
            for suffix in ("repair", "reject"):
                session.add(
                    AgentTurnRecord(
                        id=f"turn-{suffix}",
                        thread_id="review-thread",
                        agent_version_id="review-version",
                        trigger_type="user_message",
                        status="completed",
                        created_at=now,
                        updated_at=now,
                        completed_at=now,
                    )
                )
                session.add(
                    InteractionTraceRecord(
                        id=f"trace-{suffix}",
                        user_id="user-review-metrics",
                        trigger_type="user_message",
                        agent_turn_id=f"turn-{suffix}",
                        agent_version_id="review-version",
                        reply_kind="agent",
                        generation_status=("degraded" if suffix == "reject" else "succeeded"),
                        delivery_status="accepted",
                        created_at=now,
                        completed_at=now,
                    )
                )

            repair_payload: dict[str, object] = {
                "verdict": "repair",
                "repair_target": "response_style",
                "issue_type": "changed_meaning",
                "reason_summary": "must remain private",
                "reviewed_artifact_ids": ["candidate-original"],
                "repair_budget": {"max_upstream_repairs": 2},
            }
            pass_payload: dict[str, object] = {
                "outcome": "pass",
                "reviewed_artifact_ids": ["candidate-repaired"],
                "repair_budget": {"max_upstream_repairs": 2},
            }
            reject_payload: dict[str, object] = {
                "status": "reject",
                "issue_type": "medical_overreach",
                "reason": "must remain private",
                "reviewed_artifact_ids": ["candidate-rejected"],
            }
            session.add_all(
                (
                    invocation(
                        "review-repair-1",
                        trace_id="trace-repair",
                        turn_id="turn-repair",
                        attempt=1,
                        output_artifact_id="verdict-repair",
                    ),
                    invocation(
                        "review-repair-2",
                        trace_id="trace-repair",
                        turn_id="turn-repair",
                        attempt=2,
                        output_artifact_id="verdict-pass",
                    ),
                    invocation(
                        "review-reject-1",
                        trace_id="trace-reject",
                        turn_id="turn-reject",
                        attempt=1,
                        output_artifact_id="verdict-reject",
                    ),
                    artifact(
                        "verdict-repair",
                        turn_id="turn-repair",
                        invocation_id="review-repair-1",
                        payload=repair_payload,
                        parent_id="candidate-original",
                    ),
                    artifact(
                        "verdict-pass",
                        turn_id="turn-repair",
                        invocation_id="review-repair-2",
                        payload=pass_payload,
                        parent_id="candidate-repaired",
                    ),
                    artifact(
                        "verdict-reject",
                        turn_id="turn-reject",
                        invocation_id="review-reject-1",
                        payload=reject_payload,
                        parent_id="candidate-rejected",
                    ),
                    AgentItemRecord(
                        id="review-transition-repair",
                        thread_id="review-thread",
                        turn_id="turn-repair",
                        sequence=1,
                        item_type="workflow_transition",
                        status="completed",
                        payload_json=json.dumps(
                            {
                                "from_node": "review_running",
                                "to_node": "style_running",
                                "transition_type": "route",
                                "reason_code": "review_repair",
                                "attempt": 1,
                            }
                        ),
                        created_at=now,
                    ),
                    AgentItemRecord(
                        id="review-transition-reject",
                        thread_id="review-thread",
                        turn_id="turn-reject",
                        sequence=1,
                        item_type="workflow_transition",
                        status="completed",
                        payload_json=json.dumps(
                            {
                                "from_node": "review_running",
                                "to_node": "neutral_fallback",
                                "transition_type": "route",
                                "reason_code": "review_rejected",
                                "attempt": 1,
                            }
                        ),
                        created_at=now,
                    ),
                )
            )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        ) as http:
            unauthorized = await http.get("/api/admin/metrics/workflows")
            await http.post(
                "/api/admin/auth/login",
                json={"username": "operator", "password": "a-long-test-password"},
            )
            metrics = await http.get("/api/admin/metrics/workflows?window_days=7")
            invalid_window = await http.get(
                "/api/admin/metrics/workflows?window_days=0"
            )

    assert unauthorized.status_code == 401
    assert invalid_window.status_code == 422
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["window"]["days"] == 7
    assert payload["counts"] == {
        "trace_count": 2,
        "workflow_count": 2,
        "reviewed_workflow_count": 2,
        "reviewer_invocation_count": 3,
        "verdict_count": 3,
        "issue_count": 2,
        "rejected_workflow_count": 1,
        "repair_workflow_count": 1,
        "degraded_workflow_count": 1,
        "repair_attempt_count": 1,
    }
    assert payload["rates"] == {
        "rejection_rate": 0.5,
        "repair_rate": 0.5,
        "degradation_rate": 0.5,
    }
    assert payload["denominators"] == {
        "rejection_rate": 2,
        "repair_rate": 2,
        "degradation_rate": 2,
    }
    assert payload["by_repair_target"] == {
        "orchestrator": 0,
        "nutrition_expert": 0,
        "response_style": 1,
        "unknown": 0,
    }
    assert "must remain private" not in metrics.text
