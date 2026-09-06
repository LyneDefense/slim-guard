from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.contracts import payload_sha256
from slim_guard.db.models import AgentArtifactRecord


def test_style_summary_explains_a_policy_bypass() -> None:
    summary = AdminQueryRepository._style_summary(
        artifacts=[],
        transitions=[
            {
                "from_node": "response_rendering",
                "to_node": "output_guarded",
                "transition_type": "route",
                "reason_code": "style_bypassed",
                "attempt": 1,
            }
        ],
        adopted={},
        degraded_events=[],
    )

    assert summary == {
        "style_profile_version": None,
        "status": "bypassed",
        "bypassed": True,
        "bypass_reason": "style_bypassed",
        "degraded": False,
        "degraded_reason": None,
        "adopted": False,
        "adopted_artifact_id": None,
        "adoption_mode": None,
        "final": None,
    }


def test_style_summary_distinguishes_degraded_output_from_adoption() -> None:
    summary = AdminQueryRepository._style_summary(
        artifacts=[
            {
                "artifact_id": "styled-1",
                "artifact_type": "neutral_response",
                "style_profile_version": "slimguard_default_v1",
            }
        ],
        transitions=[
            {
                "from_node": "style_running",
                "to_node": "neutral_fallback",
                "transition_type": "route",
                "reason_code": "style_failed",
                "attempt": 1,
            }
        ],
        adopted={},
        degraded_events=[
            {
                "details": {
                    "artifact_id": None,
                    "reason_code": "style_timeout",
                    "fallback_type": "neutral_renderer",
                }
            }
        ],
    )

    assert summary["style_profile_version"] == "slimguard_default_v1"
    assert summary["status"] == "degraded"
    assert summary["degraded"] is True
    assert summary["degraded_reason"] == "style_timeout"
    assert summary["adopted"] is False


def test_response_plan_artifact_hides_block_text_from_admin_payload() -> None:
    payload = {
        "schema_version": "1",
        "communication_act": "acknowledge",
        "requested_detail": "short",
        "content_blocks": [
            {
                "block_id": "fact-1",
                "kind": "fact",
                "text": "用户敏感健康原文",
                "source_refs": ["record-1"],
                "required": True,
            }
        ],
        "citation_refs": [],
        "prohibited_transformations": [],
    }
    row = AgentArtifactRecord(
        id="artifact-plan",
        turn_id="turn-1",
        invocation_id=None,
        producer_role="coordinator",
        artifact_type="response_plan",
        schema_version="1",
        parent_artifact_ids_json="[]",
        payload_sha256=payload_sha256(payload),
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=datetime(2026, 9, 6, tzinfo=UTC),
    )

    view = AdminQueryRepository._artifact_view(row)

    assert view["payload"]["content_blocks"] == [
        {
            "block_id": "fact-1",
            "kind": "fact",
            "source_refs": ["record-1"],
            "required": True,
        }
    ]
    assert "用户敏感健康原文" not in json.dumps(view["payload"], ensure_ascii=False)
