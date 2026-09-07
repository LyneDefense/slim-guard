from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.agents.contracts import (
    AgentInvocation,
    KnowledgeCitation,
    ProfessionalAssessment,
    ProfessionalClaim,
)
from slim_guard.agents.nutrition import (
    CitationValidationPolicy,
    KnowledgeCandidate,
    KnowledgeCandidateBinder,
    NutritionAgent,
    NutritionCitationValidator,
    NutritionContext,
    NutritionContextCompiler,
    NutritionKnowledgeRepositoryAdapter,
    NutritionValidationIssueCode,
    bind_candidates,
)
from slim_guard.agents.nutrition.tools import EmptyNutritionKnowledgeRepository
from slim_guard.agents.structured_runner import StructuredAgentRunner


def candidate(
    suffix: str,
    *,
    active: bool = True,
    review_status: str = "approved",
    applicability: tuple[str, ...] = ("adult", "china"),
    content: str | None = None,
    adoption_status: str = "adopted",
) -> KnowledgeCandidate:
    return KnowledgeCandidate.create(
        candidate_id=f"candidate-{suffix}",
        citation_id=f"citation-{suffix}",
        source_id=f"source-{suffix}",
        chunk_id=f"chunk-{suffix}",
        title=f"资料 {suffix}",
        publisher="测试机构",
        version="2026",
        applicability=applicability,
        review_status=review_status,
        active=active,
        content=content or f"已审核的资料内容 {suffix}",
        rank=1,
        keyword_score=2.5,
        vector_score=0.8,
        rerank_score=0.9,
        match_reasons=("keyword", "semantic"),
        adoption_status=adoption_status,
    )


def nutrition_context(*candidates: KnowledgeCandidate) -> NutritionContext:
    retrieval = bind_candidates(
        "nutrition-invocation-1",
        candidates,
        policy=CitationValidationPolicy(required_applicability=("adult", "china")),
        query_summary="体重管理",
    )
    return NutritionContextCompiler().compile(
        {
            "turn_id": "turn-1",
            "user_request": "请给我有资料依据的建议",
            "professional_question": "成年人应如何管理体重？",
            "items": [],
            "missing_information": [],
        },
        knowledge=retrieval,
    )


def cited_assessment(
    context: NutritionContext,
    *,
    citation: KnowledgeCitation | None = None,
) -> ProfessionalAssessment:
    adopted = citation or context.knowledge.citations[0]
    return ProfessionalAssessment(
        assessment_type="general",
        overall="资料支持规律饮食和活动。",
        findings=(
            ProfessionalClaim(
                claim_id="claim-rag",
                category="weight_management",
                statement="规律饮食和活动有助于体重管理。",
                basis_types=("rag_evidence",),
                knowledge_refs=(adopted.citation_id,),
                confidence="medium",
            ),
        ),
        citations=(adopted,),
    )


def invocation() -> AgentInvocation:
    return AgentInvocation(
        invocation_id="nutrition-invocation-1",
        trace_id="trace-1",
        turn_id="turn-1",
        graph_version="typed-supervisor-v1",
        agent_role="nutrition_expert",
        agent_version="nutrition-assessment-v1",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        max_model_calls=2,
        max_tool_calls=0,
        max_total_tokens=4096,
    )


def model_response(assessment: ProfessionalAssessment) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=assessment.model_dump_json(),
        )
    )


def test_candidate_content_hash_detects_repository_payload_tampering() -> None:
    original = candidate("hash")
    payload = original.model_dump(mode="json")
    payload["content"] = "内容被替换"

    with pytest.raises(ValidationError, match="content hash does not match"):
        KnowledgeCandidate.model_validate(payload)


def test_binder_filters_inactive_unapproved_and_inapplicable_candidates() -> None:
    approved = candidate("approved")
    draft = candidate("draft", review_status="draft")
    inactive = candidate("inactive", active=False)
    inapplicable = candidate("child", applicability=("child", "china"))

    retrieval = KnowledgeCandidateBinder().bind_candidates(
        invocation_id="nutrition-invocation-1",
        candidates=(approved, draft, inactive, inapplicable),
        policy=CitationValidationPolicy(required_applicability=("adult", "china")),
    )

    assert len(retrieval.candidates) == 4
    assert retrieval.candidates[0].rerank_score == 0.9
    assert retrieval.citations == (approved.bind("nutrition-invocation-1"),)
    assert {
        item.candidate_id: item.reason.value for item in retrieval.rejected_candidates
    } == {
        "candidate-draft": "not_approved",
        "candidate-inactive": "inactive",
        "candidate-child": "inapplicable",
    }


def test_binder_rejects_repository_attempt_to_set_foreign_invocation() -> None:
    raw = candidate("foreign").model_dump(mode="json")
    raw["retrieved_in_invocation_id"] = "foreign-invocation"

    with pytest.raises(ValidationError, match="Extra inputs"):
        KnowledgeCandidateBinder().bind_candidates(
            invocation_id="nutrition-invocation-1",
            candidates=(raw,),
        )


async def test_repository_adapter_empty_result_remains_explicitly_empty() -> None:
    repository: NutritionKnowledgeRepositoryAdapter = EmptyNutritionKnowledgeRepository()
    raw = await repository.search(query="体重管理", max_results=5)

    retrieval = KnowledgeCandidateBinder().bind_search_result(
        invocation_id="nutrition-invocation-1",
        result=raw,
    )

    assert retrieval.corpus_status.value == "empty"
    assert retrieval.candidates == ()
    assert retrieval.citations == ()


def test_citation_validator_requires_one_valid_citation_per_rag_claim() -> None:
    context = nutrition_context(candidate("coverage"))
    assessment = cited_assessment(context)

    report = NutritionCitationValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context,
        assessment=assessment,
    )

    assert report.is_valid
    assert report.rag_claim_count == 1
    assert report.covered_rag_claim_count == 1
    assert report.coverage_percent == 100.0


def test_citation_validator_rejects_unknown_and_uncovered_rag_claim() -> None:
    context = nutrition_context(candidate("allowed"))
    foreign = candidate("foreign").bind("nutrition-invocation-1")
    assessment = cited_assessment(context, citation=foreign)

    report = NutritionCitationValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context,
        assessment=assessment,
    )

    assert report.coverage_percent == 0.0
    assert NutritionValidationIssueCode.UNKNOWN_CITATION in {
        issue.code for issue in report.issues
    }
    assert NutritionValidationIssueCode.RAG_CLAIM_UNCOVERED in {
        issue.code for issue in report.issues
    }


def test_citation_validator_rejects_changed_metadata_and_foreign_invocation() -> None:
    context = nutrition_context(candidate("integrity"))
    allowed = context.knowledge.citations[0]
    changed = KnowledgeCitation.model_validate(
        {
            **allowed.model_dump(mode="json"),
            "title": "被模型篡改的标题",
            "retrieved_in_invocation_id": "foreign-invocation",
        }
    )
    assessment = cited_assessment(context, citation=changed)

    report = NutritionCitationValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context,
        assessment=assessment,
    )
    codes = {issue.code for issue in report.issues}

    assert NutritionValidationIssueCode.CITATION_CHANGED in codes
    assert NutritionValidationIssueCode.CITATION_INVOCATION_MISMATCH in codes


def test_citation_validator_rejects_an_adopted_but_unused_citation() -> None:
    context = nutrition_context(candidate("used"), candidate("unused"))
    used, unused = context.knowledge.citations
    assessment = ProfessionalAssessment(
        assessment_type="general",
        overall="引用其中一项资料。",
        findings=(
            ProfessionalClaim(
                claim_id="claim-rag",
                category="weight_management",
                statement="资料支持的结论。",
                basis_types=("rag_evidence",),
                knowledge_refs=(used.citation_id,),
                confidence="medium",
            ),
        ),
        citations=(used, unused),
    )

    report = NutritionCitationValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context,
        assessment=assessment,
    )

    assert NutritionValidationIssueCode.UNUSED_CITATION in {
        issue.code for issue in report.issues
    }


async def test_nutrition_agent_repairs_a_changed_citation_once() -> None:
    context = nutrition_context(candidate("agent"))
    valid = cited_assessment(context)
    changed_citation = KnowledgeCitation.model_validate(
        {
            **context.knowledge.citations[0].model_dump(mode="json"),
            "publisher": "模型虚构机构",
        }
    )
    invalid = cited_assessment(context, citation=changed_citation)
    gateway = ScriptedModelGateway((model_response(invalid), model_response(valid)))
    agent = NutritionAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context)

    assert result.status.value == "succeeded"
    assert result.repair_attempted is True
    assert result.assessment == valid
    assert "citation_changed" in (gateway.requests[1].messages[-1].content or "")


async def test_nutrition_prompt_excludes_rejected_candidate_content() -> None:
    approved = candidate("safe", content="允许模型读取的内容")
    draft = candidate("draft-secret", review_status="draft", content="未审核私密内容")
    candidate_only = candidate(
        "not-selected",
        content="未入选候选池内容",
        adoption_status="candidate_only",
    )
    retrieval = KnowledgeCandidateBinder().bind_candidates(
        invocation_id="nutrition-invocation-1",
        candidates=(approved, draft, candidate_only),
    )
    context = NutritionContextCompiler().compile(
        {
            "turn_id": "turn-1",
            "user_request": "请分析",
            "professional_question": "应该怎么做？",
            "items": [],
        },
        knowledge=retrieval,
    )
    assessment = cited_assessment(context)
    gateway = ScriptedModelGateway((model_response(assessment),))
    agent = NutritionAgent(
        runner=StructuredAgentRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context)
    prompt = gateway.requests[0].messages[1].content or ""

    assert result.status.value == "succeeded"
    assert "允许模型读取的内容" in prompt
    assert "未审核私密内容" not in prompt
    assert "未入选候选池内容" not in prompt
    assert {
        item.candidate_id: item.reason.value for item in retrieval.rejected_candidates
    }[candidate_only.candidate_id] == "not_selected"
