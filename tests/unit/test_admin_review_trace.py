from __future__ import annotations

import json

from slim_guard.admin.repository import AdminQueryRepository


def test_reviewer_summary_normalizes_aliases_and_keeps_comparison_metadata_only() -> None:
    safe_verdict = AdminQueryRepository._safe_reviewer_verdict(
        {
            "schema_version": "1",
            "status": "repair",
            "target_agent": "response_style",
            "issue_type": "changed_meaning",
            "issues": [
                {
                    "code": "unsupported_claim",
                    "excerpt": "sensitive excerpt",
                    "explanation": "private reviewer explanation",
                }
            ],
            "reason": "private reviewer reason",
            "repair_attempt": 0,
            "repair_budget": {
                "max_style_repairs": 1,
                "max_nutrition_repairs": 1,
                "max_orchestrator_repairs": 1,
                "max_upstream_repairs": 2,
                "unsafe_note": "must not leak",
            },
        }
    )
    artifacts = [
        {
            "artifact_id": "original",
            "artifact_type": "StyledResponse",
            "producer_role": "response_style",
            "schema_version": "1",
            "parent_artifact_ids": [],
            "payload_sha256": "a" * 64,
            "payload": {"text": "sensitive original body"},
            "integrity_status": "verified",
            "created_at": "2026-09-08T00:00:00Z",
        },
        {
            "artifact_id": "verdict-1",
            "artifact_type": "reviewer_verdict",
            "producer_role": "response_reviewer",
            "schema_version": "1",
            "invocation_id": "review-1",
            "parent_artifact_ids": ["original"],
            "payload_sha256": "b" * 64,
            "payload": safe_verdict,
            "integrity_status": "verified",
            "created_at": "2026-09-08T00:01:00Z",
        },
        {
            "artifact_id": "repaired",
            "artifact_type": "StyledResponse",
            "producer_role": "response_style",
            "schema_version": "1",
            "invocation_id": "style-2",
            "parent_artifact_ids": ["original", "verdict-1"],
            "payload_sha256": "c" * 64,
            "payload": {"text": "sensitive repaired body"},
            "integrity_status": "verified",
            "created_at": "2026-09-08T00:02:00Z",
        },
        {
            "artifact_id": "verdict-2",
            "artifact_type": "ReviewerVerdict",
            "producer_role": "response_reviewer",
            "schema_version": "1",
            "invocation_id": "review-2",
            "parent_artifact_ids": ["repaired"],
            "payload_sha256": "d" * 64,
            "payload": {
                "outcome": "pass",
                "issue_types": [],
                "issue_count": 0,
                "issues": [],
                "reviewed_artifact_ids": ["repaired"],
            },
            "integrity_status": "verified",
            "created_at": "2026-09-08T00:03:00Z",
        },
    ]
    invocations = [
        {
            "invocation_id": "review-1",
            "agent_role": "response_reviewer",
            "attempt": 1,
            "input_artifact_ids": ["original"],
            "status": "succeeded",
        },
        {
            "invocation_id": "style-2",
            "agent_role": "response_style",
            "attempt": 2,
            "input_artifact_ids": ["original"],
            "output_artifact_id": "repaired",
            "status": "succeeded",
            "reason_summary": "review repair",
        },
        {
            "invocation_id": "review-2",
            "agent_role": "response_reviewer",
            "attempt": 2,
            "input_artifact_ids": ["repaired"],
            "status": "succeeded",
        },
    ]

    summary = AdminQueryRepository._review_summary(
        artifacts=artifacts,
        invocations=invocations,
        transitions=[
            {
                "from_node": "review_running",
                "to_node": "style_running",
                "transition_type": "route",
                "reason_code": "review_repair",
                "attempt": 1,
            }
        ],
        adopted={"artifact_id": "repaired"},
        degraded_events=[],
    )

    assert summary["status"] == "passed"
    assert summary["reviewer_invocation_count"] == 2
    assert summary["verdict_count"] == 2
    assert summary["issue_count"] == 2
    assert summary["repair_counts"] == {
        "orchestrator": 0,
        "nutrition_expert": 0,
        "response_style": 1,
        "unknown": 0,
        "total": 1,
    }
    assert summary["budget"]["configured_limits"]["max_upstream_repairs"] == 2
    assert summary["comparison"]["changed"] is True
    assert summary["comparison"]["original"]["artifact_id"] == "original"
    assert summary["comparison"]["repaired"][0]["artifact_id"] == "repaired"
    comparison_json = json.dumps(summary["comparison"])
    for forbidden in (
        '"body":',
        '"payload":',
        '"reason":',
        '"excerpt":',
        '"explanation":',
        "sensitive",
        "private",
    ):
        assert forbidden not in comparison_json


def test_budget_denied_repair_is_degraded_but_not_counted_as_executed() -> None:
    summary = AdminQueryRepository._review_summary(
        artifacts=[
            {
                "artifact_id": "verdict-budget",
                "artifact_type": "reviewer_verdict",
                "producer_role": "response_reviewer",
                "schema_version": "1",
                "parent_artifact_ids": ["candidate"],
                "payload": AdminQueryRepository._safe_reviewer_verdict(
                    {
                        "verdict": "repair",
                        "repair_target": "nutrition_expert",
                        "issue_type": "unsupported_claim",
                        "repair_budget": {"max_upstream_repairs": 1},
                    }
                ),
                "integrity_status": "verified",
            }
        ],
        invocations=[
            {
                "invocation_id": "review-budget",
                "agent_role": "response_reviewer",
                "attempt": 2,
                "status": "succeeded",
            }
        ],
        transitions=[
            {
                "from_node": "review_running",
                "to_node": "neutral_fallback",
                "transition_type": "route",
                "reason_code": "budget_exhausted",
                "attempt": 2,
            }
        ],
        adopted={},
        degraded_events=[],
    )

    assert summary["status"] == "degraded"
    assert summary["degraded"] is True
    assert summary["repair_attempts"] == []
    assert summary["repair_counts"]["total"] == 0
    assert summary["budget"] == {
        "exhausted": True,
        "exhausted_targets": ["nutrition_expert"],
        "repair_attempts_observed": 0,
        "configured_limits": {"max_upstream_repairs": 1},
    }
