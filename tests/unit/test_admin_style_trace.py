from __future__ import annotations

import json
from datetime import UTC, datetime

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.contracts import payload_sha256
from slim_guard.db.models import AgentArtifactRecord, AgentItemRecord, AgentItemRedactionRecord


def test_turn_input_view_shows_current_input_without_bypassing_redaction() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    visible = AgentItemRecord(
        id="visible-message",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=1,
        item_type="user_message",
        status="completed",
        payload_json=json.dumps({"text": "这顿饭能吃吗？", "occurred_at": "2026-09-17T12:00:00Z"}),
        created_at=now,
    )
    redacted = AgentItemRecord(
        id="redacted-message",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=2,
        item_type="user_message",
        status="completed",
        payload_json=json.dumps({"text": "不应展示的原文"}),
        created_at=now,
    )
    image = AgentItemRecord(
        id="image-1",
        thread_id="thread-1",
        turn_id="turn-1",
        sequence=3,
        item_type="image_attachment",
        status="completed",
        payload_json=json.dumps({"asset_id": "asset-1", "mime_type": "image/jpeg"}),
        created_at=now,
    )
    redaction = AgentItemRedactionRecord(
        item_id=redacted.id,
        original_payload_sha256="0" * 64,
        policy_version="retention-v1",
        redacted_at=now,
    )

    result = AdminQueryRepository._turn_input_view(
        [(visible, None), (redacted, redaction), (image, None)]
    )

    assert result["messages"][0]["text"] == "这顿饭能吃吗？"
    assert result["messages"][1]["text"] is None
    assert result["messages"][1]["redacted"] is True
    assert result["images"][0]["asset_id"] == "asset-1"
    assert "不应展示的原文" not in json.dumps(result, ensure_ascii=False, default=str)


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


def test_nutrition_inputs_expose_frozen_rag_provenance_without_chunk_content() -> None:
    payload = {
        "observations": [],
        "knowledge": {
            "corpus_status": "available",
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "citation_id": "citation-1",
                    "source_id": "source-1",
                    "chunk_id": "chunk-1",
                    "title": "营养指南",
                    "content": "不应出现在默认后台摘要里的检索正文",
                    "corpus_release_id": "release-3",
                    "retrieval_run_id": "run-1",
                    "adoption_status": "selected",
                }
            ],
            "citations": [],
            "query_summary": "用户敏感检索问题",
        },
        "tool_receipts": [{"output": {"secret": "不应显示的工具原始输出"}}],
        "knowledge_snapshot": {
            "corpus_release_id": "release-3",
            "corpus_release_version": "nutrition_v3",
            "corpus_manifest_sha256": "a" * 64,
            "retrieval_profile_id": "retrieval-profile-1",
            "embedding_profile_id": "embedding-profile-1",
            "lexical_profile_id": "lexical-profile-1",
            "chunker_profile_id": "chunker-profile-1",
            "unexpected_secret": "must-not-survive",
        },
    }
    row = AgentArtifactRecord(
        id="nutrition-inputs-1",
        turn_id="turn-1",
        invocation_id="nutrition-invocation-1",
        producer_role="nutrition_tool",
        artifact_type="nutrition_inputs",
        schema_version="1",
        parent_artifact_ids_json="[]",
        payload_sha256=payload_sha256(payload),
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )

    view = AdminQueryRepository._artifact_view(row)

    assert view["body_redacted"] is True
    assert view["payload"]["knowledge_snapshot"] == {
        "corpus_release_id": "release-3",
        "corpus_release_version": "nutrition_v3",
        "corpus_manifest_sha256": "a" * 64,
        "retrieval_profile_id": "retrieval-profile-1",
        "embedding_profile_id": "embedding-profile-1",
        "lexical_profile_id": "lexical-profile-1",
        "chunker_profile_id": "chunker-profile-1",
    }
    candidate = view["payload"]["knowledge"]["candidates"][0]
    assert candidate["retrieval_run_id"] == "run-1"
    assert candidate["corpus_release_id"] == "release-3"
    serialized = json.dumps(view["payload"], ensure_ascii=False)
    assert "检索正文" not in serialized
    assert "用户敏感检索问题" not in serialized
    assert "工具原始输出" not in serialized
    assert "must-not-survive" not in serialized


def test_style_comparison_exposes_only_deliberate_drafts_and_render_attempts() -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)

    def artifact(
        artifact_id: str,
        *,
        producer: str,
        artifact_type: str,
        payload: dict[str, object],
    ) -> AgentArtifactRecord:
        return AgentArtifactRecord(
            id=artifact_id,
            turn_id="turn-1",
            invocation_id=None,
            producer_role=producer,
            artifact_type=artifact_type,
            schema_version="1",
            parent_artifact_ids_json="[]",
            payload_sha256=payload_sha256(payload),
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=now,
        )

    rows = (
        artifact(
            "resolution-1",
            producer="style_resolver",
            artifact_type="style_resolution",
            payload={"profile_version": "doctor_strict_v3"},
        ),
        artifact(
            "neutral-1",
            producer="core",
            artifact_type="neutral_response",
            payload={"text": "中性内容稿", "hidden_reasoning": "绝不能展示"},
        ),
        artifact(
            "styled-1",
            producer="response_style",
            artifact_type="styled_response",
            payload={
                "text": "第一次表达",
                "attempt": 1,
                "style_profile_version": "doctor_strict_v3",
            },
        ),
        artifact(
            "styled-2",
            producer="response_style",
            artifact_type="styled_response",
            payload={
                "text": "修订后表达",
                "attempt": 2,
                "style_profile_version": "doctor_strict_v3",
            },
        ),
    )
    timeline = [
        {
            "event_type": "agent_item",
            "operation": "response_adopted",
            "details": {"artifact_id": "styled-2", "final": True},
        }
    ]

    comparison = AdminQueryRepository._style_comparison_view(
        artifact_rows=rows,
        timeline=timeline,
        output={"content": "修订后表达"},
    )

    assert comparison is not None
    assert comparison["profile_version"] == "doctor_strict_v3"
    assert comparison["neutral_text"] == "中性内容稿"
    assert [item["attempt"] for item in comparison["renders"]] == [1, 2]
    assert comparison["final_artifact_id"] == "styled-2"
    assert "绝不能展示" not in json.dumps(comparison, ensure_ascii=False, default=str)
