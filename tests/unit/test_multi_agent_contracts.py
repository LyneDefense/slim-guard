from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from slim_guard.agents.contracts import (
    AgentInvocation,
    ArtifactProducerRole,
    ClaimBasis,
    KnowledgeCitation,
    ProfessionalAction,
    ProfessionalAssessment,
    ProfessionalClaim,
    ResponseContentBlock,
    ResponsePlan,
    ReviewerVerdict,
    StyledResponse,
    TurnDirective,
)


def test_professional_directive_requires_a_question_and_evidence() -> None:
    with pytest.raises(ValidationError, match="professional_question"):
        TurnDirective(
            response_path="professional_assessment",
            interaction_kind="question",
            user_need_summary="需要分析午餐",
            response_brief="分析结构和下一步",
            evidence_refs=("meal-1",),
            voice_act="explain",
        )


def test_contracts_are_frozen_and_reject_unknown_fields() -> None:
    directive = TurnDirective(
        response_path="direct",
        interaction_kind="chat",
        user_need_summary="普通交流",
        response_brief="简短回应",
        voice_act="acknowledge",
    )

    with pytest.raises(ValidationError, match="frozen"):
        directive.response_brief = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        TurnDirective.model_validate({**directive.model_dump(), "secret": "not allowed"})


def test_rag_claim_must_resolve_to_a_real_citation() -> None:
    claim = ProfessionalClaim(
        claim_id="claim-1",
        category="vegetables",
        statement="蔬菜摄入偏少",
        basis_types=(ClaimBasis.RAG_EVIDENCE,),
        knowledge_refs=("citation-1",),
        confidence="medium",
    )

    with pytest.raises(ValidationError, match="unknown knowledge citations"):
        ProfessionalAssessment(
            assessment_type="meal",
            overall="结构需要调整",
            findings=(claim,),
        )

    citation = KnowledgeCitation(
        citation_id="citation-1",
        source_id="source-1",
        chunk_id="chunk-1",
        title="膳食指南",
        publisher="测试机构",
        version="2026",
        review_status="approved",
        retrieved_in_invocation_id="invocation-1",
    )
    assessment = ProfessionalAssessment(
        assessment_type="meal",
        overall="结构需要调整",
        findings=(claim,),
        actions=(
            ProfessionalAction(
                action_id="action-1",
                statement="下一餐增加蔬菜",
                basis_claim_ids=("claim-1",),
            ),
        ),
        citations=(citation,),
    )

    assessment.validate_citation_invocation("invocation-1")
    with pytest.raises(ValueError, match="another invocation"):
        assessment.validate_citation_invocation("forged-invocation")


def test_response_plan_and_styled_response_preserve_required_blocks_and_citations() -> None:
    plan = ResponsePlan(
        communication_act="explain",
        content_blocks=(
            ResponseContentBlock(
                block_id="claim-1",
                kind="claim",
                text="蔬菜偏少",
                source_refs=("claim-1",),
            ),
        ),
        citation_refs=("citation-1",),
        prohibited_transformations=("do_not_strengthen_uncertainty",),
    )
    valid = StyledResponse(
        text="这餐蔬菜偏少。",
        used_block_ids=("claim-1",),
        used_claim_ids=("claim-1",),
        preserved_citation_refs=("citation-1",),
        style_profile_version="slimguard_default_v1",
    )
    valid.validate_against_plan(plan)

    omitted = valid.model_copy(update={"used_block_ids": (), "preserved_citation_refs": ()})
    with pytest.raises(ValueError, match="omitted required blocks"):
        omitted.validate_against_plan(plan)


def test_reviewer_repair_target_must_match_issue_type() -> None:
    verdict = ReviewerVerdict(
        verdict="repair",
        repair_target="response_style",
        issue_type="style_drift",
        reason_summary="表达偏离配置",
    )
    assert verdict.repair_target.value == "response_style"

    with pytest.raises(ValidationError, match="cannot be repaired"):
        ReviewerVerdict(
            verdict="repair",
            repair_target="nutrition_expert",
            issue_type="style_drift",
            reason_summary="错误路由",
        )


def test_invocation_accepts_legacy_envelope_aliases_but_keeps_trusted_fields() -> None:
    invocation = AgentInvocation(
        invocation_id="invocation-1",
        trace_id="trace-1",
        thread_id="thread-1",
        turn_id="turn-1",
        graph_version="typed-supervisor-v1",
        callee="nutrition_expert",
        agent_version="nutrition-v1",
        parent_artifact_ids=("artifact-1",),
        allowed_tool_names=("calculate_bmi",),
        privacy_scopes=("health_summary",),
        deadline_at=datetime.now(UTC),
        max_model_calls=2,
        max_tool_calls=3,
        max_total_tokens=8000,
    )

    assert invocation.agent_role.value == "nutrition_expert"
    assert invocation.input_artifact_ids == ("artifact-1",)
    assert invocation.allowed_tools == ("calculate_bmi",)


def test_artifact_factory_computes_and_validates_canonical_hash() -> None:
    from slim_guard.agents.contracts import AgentArtifact

    artifact = AgentArtifact.create(
        artifact_id="artifact-1",
        turn_id="turn-1",
        producer_role=ArtifactProducerRole.ORCHESTRATOR,
        artifact_type="TurnDirective",
        schema_version="1",
        payload={"b": 2, "a": 1},
        created_at=datetime.now(UTC),
    )

    assert artifact.verify_payload() is True
    with pytest.raises(ValidationError, match="does not match"):
        AgentArtifact.model_validate({**artifact.model_dump(), "payload": {"a": 2}})
