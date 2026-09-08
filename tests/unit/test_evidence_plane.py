from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.nutrition.tools import (
    EmptyNutritionKnowledgeRepository,
    NutritionToolRegistry,
    calculate_bmi,
    calculate_weight_trend,
    compare_checkin_adherence,
)
from slim_guard.orchestration.evidence import (
    EvidenceAuthority,
    EvidenceBuilder,
    EvidenceConfidence,
    EvidencePacket,
    EvidenceSourceType,
)


@pytest.mark.asyncio
async def test_evidence_builder_uses_only_allowlisted_context_and_marks_vision_uncertain() -> None:
    packet = await EvidenceBuilder().build(
        turn_id="turn-1",
        user_request="帮我看看今天的进展",
        professional_question="哪些事实能够支持进展判断？",
        current_items=(
            {
                "id": "item-user",
                "item_type": "user_message",
                "payload": {"text": "我今天走了很多路"},
            },
            {
                "id": "item-image",
                "item_type": "image_attachment",
                "payload": {
                    "asset_id": "asset-1",
                    "observation": "盘中可能有蔬菜",
                },
            },
        ),
        authoritative_context={
            "recent_weights": [
                {
                    "id": "weight-1",
                    "weight_kg": "70.1",
                    "measured_at": "2026-09-06T08:00:00+08:00",
                }
            ],
            "recent_dialogue": [{"text": "不应进入证据包"}],
            "arbitrary_secret": "not allowlisted",
        },
    )

    assert {item.source_type for item in packet.items} == {
        EvidenceSourceType.USER_MESSAGE,
        EvidenceSourceType.VISION_OBSERVATION,
        EvidenceSourceType.WEIGHT_RECORD,
    }
    visual = next(
        item for item in packet.items if item.source_type is EvidenceSourceType.VISION_OBSERVATION
    )
    assert visual.authority is EvidenceAuthority.OBSERVATION
    assert visual.confidence is EvidenceConfidence.LOW
    assert visual.uncertainty == "Visual interpretation requires user confirmation"
    serialized = json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)
    assert "不应进入证据包" not in serialized
    assert "not allowlisted" not in serialized


@pytest.mark.asyncio
async def test_evidence_builder_filters_by_source_ref_and_enforces_capacity() -> None:
    packet = await EvidenceBuilder(max_items=1).build(
        turn_id="turn-1",
        user_request="分析趋势",
        professional_question="体重趋势是什么？",
        current_items=(),
        authoritative_context={
            "recent_weights": [
                {"id": "weight-1", "weight_kg": 70},
                {"id": "weight-2", "weight_kg": 69},
            ]
        },
        allowed_evidence_refs=("weight-2", "missing-1"),
    )

    assert len(packet.items) == 1
    assert packet.items[0].source_ref == "weight-2"
    assert "unresolved_evidence_ref:missing-1" in packet.missing_information
    assert packet.require_refs(packet.evidence_ids) == packet.items
    with pytest.raises(ValueError, match="Unknown evidence"):
        packet.require_refs(("not-present",))


def test_evidence_packet_rejects_visual_fact_without_uncertainty() -> None:
    with pytest.raises(ValueError, match="requires confidence"):
        EvidencePacket.model_validate(
            {
                "turn_id": "turn-1",
                "user_request": "看看图片",
                "professional_question": "图片中有什么？",
                "items": [
                    {
                        "evidence_id": "evidence-1",
                        "source_type": "vision_observation",
                        "authority": "observation",
                        "content": {"observation": "食物"},
                    }
                ],
            }
        )


def test_nutrition_calculations_are_deterministic() -> None:
    assert calculate_bmi(weight_kg=70, height_cm=175) == {
        "calculation_type": "bmi",
        "value": 22.9,
        "unit": "kg/m2",
        "category": "reference_range",
        "inputs": {"weight_kg": 70.0, "height_cm": 175.0},
        "interpretation_scope": "screening_observation_only",
    }
    trend = calculate_weight_trend(
        measurements=(
            {
                "weight_kg": 69,
                "measured_at": datetime(2026, 9, 8, tzinfo=UTC),
                "evidence_id": "weight-2",
            },
            {
                "weight_kg": 70,
                "measured_at": datetime(2026, 9, 1, tzinfo=UTC),
                "evidence_id": "weight-1",
            },
        )
    )
    assert trend["change_kg"] == -1.0
    assert trend["rate_kg_per_week"] == -1.0
    assert trend["evidence_refs"] == ["weight-1", "weight-2"]
    adherence = compare_checkin_adherence(
        expected_checkins=("weight:1", "meal:1"),
        completed_checkins=("weight:1", "extra"),
    )
    assert adherence["completion_rate_percent"] == 50.0
    assert adherence["missed_checkins"] == ["meal:1"]


@pytest.mark.asyncio
async def test_nutrition_registry_is_read_only_and_empty_corpus_never_has_articles() -> None:
    registry = NutritionToolRegistry()
    assert registry.knowledge_configured is False
    assert NutritionToolRegistry(EmptyNutritionKnowledgeRepository()).knowledge_configured is True

    assert all(registry.resolve(name).effect_level == "read" for name in registry.names)
    search = await registry.execute(
        "search_nutrition_knowledge",
        {"query": "蛋白质建议", "max_results": 5},
    )
    assert search.status == "succeeded"
    assert search.output == {
        "corpus_status": "empty",
        "citations": [],
        "query_summary": "No approved nutrition corpus is configured",
    }
    assert "article" not in search.to_model_content().lower()
    unknown = await registry.execute("write_meal", {})
    assert unknown.status == "failed"
    assert unknown.failure is not None
    assert unknown.failure.code == "unknown_nutrition_tool"


def test_admin_professional_artifacts_hide_bodies_but_keep_provenance() -> None:
    evidence = AdminQueryRepository._professional_artifact_payload(
        artifact_type="evidence_packet",
        payload={
            "schema_version": "1",
            "turn_id": "turn-1",
            "user_request": "用户敏感原话",
            "professional_question": "敏感专业问题",
            "items": [
                {
                    "evidence_id": "evidence-1",
                    "source_type": "vision_observation",
                    "authority": "observation",
                    "content": {"observation": "敏感图片观察", "asset_id": "asset-1"},
                    "confidence": "low",
                    "uncertainty": "可能识别错误",
                    "source_ref": "asset-1",
                }
            ],
            "missing_information": ["需要用户确认"],
        },
    )
    assessment = AdminQueryRepository._professional_artifact_payload(
        artifact_type="professional_assessment",
        payload={
            "schema_version": "1",
            "assessment_type": "meal",
            "overall": "敏感总结",
            "findings": [
                {
                    "claim_id": "claim-1",
                    "category": "meal_balance",
                    "statement": "敏感专业结论",
                    "basis_types": ["visual_observation"],
                    "evidence_refs": ["evidence-1"],
                    "knowledge_refs": [],
                    "confidence": "low",
                }
            ],
            "actions": [
                {
                    "action_id": "action-1",
                    "statement": "敏感行动正文",
                    "basis_claim_ids": ["claim-1"],
                }
            ],
            "citations": [],
        },
    )

    assert evidence["user_request"] is None
    assert evidence["items"][0]["content"] == {
        "asset_id": None,
        "observation": None,
    }
    assert evidence["items"][0]["uncertainty"] == "present"
    assert assessment["findings"][0]["statement"] is None
    assert assessment["findings"][0]["evidence_refs"] == ["evidence-1"]
    assert assessment["actions"][0]["statement"] is None
    serialized = json.dumps({"evidence": evidence, "assessment": assessment}, ensure_ascii=False)
    for secret in ("用户敏感原话", "敏感图片观察", "敏感专业结论", "敏感行动正文"):
        assert secret not in serialized


def test_admin_evidence_summary_reports_broken_reference_edges() -> None:
    summary = AdminQueryRepository._evidence_summary(
        [
            {
                "artifact_id": "artifact-evidence",
                "artifact_type": "EvidencePacket",
                "payload": {
                    "items": [
                        {
                            "evidence_id": "evidence-1",
                            "source_type": "weight_record",
                            "uncertainty": None,
                        }
                    ],
                    "missing_information": [],
                },
            },
            {
                "artifact_id": "artifact-observations",
                "artifact_type": "NutritionObservations",
                "payload": {
                    "evidence": [],
                    "calculations": [],
                    "knowledge": {"corpus_status": "empty", "citations": []},
                },
            },
            {
                "artifact_id": "artifact-assessment",
                "artifact_type": "ProfessionalAssessment",
                "payload": {
                    "findings": [
                        {
                            "claim_id": "claim-1",
                            "evidence_refs": ["evidence-missing"],
                            "knowledge_refs": ["citation-missing"],
                        }
                    ],
                    "actions": [{"action_id": "action-1", "basis_claim_ids": ["claim-missing"]}],
                    "citations": [],
                },
            },
        ]
    )

    assert summary["packet_artifact_id"] == "artifact-evidence"
    assert summary["knowledge"] == {"corpus_status": "empty", "citations": []}
    assert summary["unresolved_evidence_refs"] == ["evidence-missing"]
    assert summary["unresolved_knowledge_refs"] == ["citation-missing"]
    assert summary["unresolved_claim_refs"] == ["claim-missing"]


def test_admin_rag_view_keeps_audit_metadata_but_hides_candidate_content() -> None:
    safe = AdminQueryRepository._professional_artifact_payload(
        artifact_type="nutrition_observations",
        payload={
            "observations": [],
            "knowledge": {
                "corpus_status": "available",
                "query_summary": "敏感用户查询",
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "citation_id": "citation-1",
                        "source_id": "source-1",
                        "chunk_id": "chunk-1",
                        "title": "中国居民膳食指南",
                        "publisher": "权威机构",
                        "version": "2026",
                        "section_or_page": "第二章",
                        "source_url": "https://example.test/guide",
                        "applicability": ["adult", "china"],
                        "review_status": "approved",
                        "active": True,
                        "content_sha256": "a" * 64,
                        "content": "不应默认展示的完整专业片段",
                        "rank": 1,
                        "keyword_score": 0.8,
                        "vector_score": 0.6,
                        "rerank_score": 0.9,
                        "match_reasons": ["content_token_match"],
                        "adoption_status": "adopted",
                    }
                ],
                "citations": [],
            },
        },
    )

    assert safe["knowledge"]["query_summary"] is None
    candidate = safe["knowledge"]["candidates"][0]
    assert candidate["title"] == "中国居民膳食指南"
    assert candidate["applicability"] == ["adult", "china"]
    assert candidate["rerank_score"] == 0.9
    assert candidate["content"] is None
    serialized = json.dumps(safe, ensure_ascii=False)
    assert "敏感用户查询" not in serialized
    assert "不应默认展示的完整专业片段" not in serialized
