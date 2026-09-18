from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from slim_guard.agent_models.errors import ModelTimeoutError
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse, ModelUsage
from slim_guard.agents.contracts import (
    AgentInvocation,
    Confidence,
    KnowledgeCitation,
    ProfessionalAction,
    ProfessionalAssessment,
    ProfessionalClaim,
)
from slim_guard.agents.nutrition import (
    NUTRITION_AGENT_PROMPT,
    ConservativeNutritionFallback,
    KnowledgeCandidate,
    NutritionAgent,
    NutritionAssessmentValidator,
    NutritionContext,
    NutritionContextCompiler,
    NutritionValidationIssueCode,
    bind_candidates,
)
from slim_guard.agents.nutrition.specialist import (
    NutritionConsultationRequest,
    NutritionSpecialist,
)
from slim_guard.agents.nutrition.tools import NutritionToolRegistry
from slim_guard.nutrition_rag import NutritionRuntimeSnapshot
from slim_guard.runtime.invocation import InvocationRunner


@dataclass(frozen=True)
class PacketEvidence:
    evidence_id: str
    source_type: str
    authority: str
    content: dict[str, Any]
    confidence: str | None = None
    uncertainty: str | None = None


@dataclass(frozen=True)
class PacketLike:
    turn_id: str
    user_request: str
    professional_question: str
    items: tuple[object, ...]
    missing_information: tuple[str, ...] = ()


def packet(*, visual: bool = False) -> dict[str, object]:
    items: list[dict[str, object]] = [
        {
            "evidence_id": "weight-1",
            "source_type": "weight_record",
            "authority": "authoritative",
            "content": {"weight_kg": 77.6},
            "confidence": "high",
            "private_database_row": "must-not-be-projected",
        }
    ]
    if visual:
        items.append(
            {
                "id": "visual-1",
                "kind": "image_observation",
                "authority": "observation",
                "content": {"description": "看起来蔬菜偏少"},
                "confidence": "low",
                "uncertainty": "图片边缘模糊",
            }
        )
    return {
        "turn_id": "turn-1",
        "user_request": "看看我最近的情况",
        "professional_question": "近期体重趋势如何？",
        "items": items,
        "missing_information": ["还缺少连续七天数据"],
        "raw_conversation": "must-not-be-projected",
    }


def context(*, visual: bool = False) -> NutritionContext:
    return NutritionContextCompiler().compile(
        packet(visual=visual),
        calculation_observations=(
            {
                "observation_id": "bmi-calc-1",
                "calculation_type": "calculate_bmi",
                "value": 23.8,
                "unit": "kg/m2",
                "inputs": {"weight_kg": 77.6, "height_m": 1.806},
            },
        ),
        knowledge={"corpus_status": "empty", "citations": []},
    )


def invocation(**updates: object) -> AgentInvocation:
    values: dict[str, object] = {
        "invocation_id": "nutrition-invocation-1",
        "trace_id": "trace-1",
        "turn_id": "turn-1",
        "graph_version": "core-primary-v1",
        "agent_role": "nutrition_expert",
        "agent_version": "nutrition-v1",
        "deadline_at": datetime.now(UTC) + timedelta(seconds=30),
        "max_model_calls": 2,
        "max_tool_calls": 0,
        "max_total_tokens": 4096,
    }
    values.update(updates)
    return AgentInvocation.model_validate(values)


def valid_assessment() -> ProfessionalAssessment:
    return ProfessionalAssessment(
        assessment_type="progress",
        overall="当前体重记录可用于计算，但长期趋势数据还不完整。",
        findings=(
            ProfessionalClaim(
                claim_id="claim-weight",
                category="current_weight",
                statement="本次记录体重为 77.6kg",
                basis_types=("user_evidence",),
                evidence_refs=("weight-1",),
                confidence="high",
            ),
            ProfessionalClaim(
                claim_id="claim-bmi",
                category="bmi",
                statement="确定性计算得到 BMI 23.8",
                basis_types=("deterministic_calculation",),
                evidence_refs=("bmi-calc-1",),
                confidence="high",
            ),
        ),
        actions=(
            ProfessionalAction(
                action_id="action-continue",
                statement="继续积累连续记录后再判断趋势",
                basis_claim_ids=("claim-weight",),
            ),
        ),
        questions=("能否继续记录后续体重？",),
        uncertainty_note="现有数据不足以判断长期趋势。",
    )


def model_response(
    assessment: ProfessionalAssessment,
    *,
    tokens: int = 60,
) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            content=assessment.model_dump_json(),
        ),
        usage=ModelUsage(total_tokens=tokens),
    )


def test_context_compiler_projects_only_typed_evidence_and_read_observations() -> None:
    compiled = context(visual=True)
    payload = compiled.model_dump(mode="json")

    assert compiled.available_evidence_ids == {"weight-1", "visual-1", "bmi-calc-1"}
    assert compiled.knowledge.corpus_status.value == "empty"
    assert payload["evidence"][0] == {
        "evidence_id": "weight-1",
        "source_type": "weight_record",
        "authority": "authoritative",
        "content": {"weight_kg": 77.6},
        "confidence": "high",
        "uncertainty": None,
    }
    assert "raw_conversation" not in payload
    assert "private_database_row" not in payload["evidence"][0]


def test_context_compiler_accepts_an_evidence_packet_protocol_object() -> None:
    canonical = PacketLike(
        turn_id="turn-1",
        user_request="帮我看看午餐",
        professional_question="午餐结构如何？",
        items=(
            PacketEvidence(
                evidence_id="visual-canonical-1",
                source_type="vision_observation",
                authority="observation",
                content={"description": "看起来有一份米饭"},
                confidence="low",
                uncertainty="图片无法确认实际重量",
            ),
        ),
    )

    compiled = NutritionContextCompiler().compile(canonical)

    assert compiled.evidence[0].source_type == "vision_observation"
    assert compiled.evidence[0].authority.value == "observation"
    assert compiled.evidence[0].confidence is not None
    assert compiled.evidence[0].confidence.value == "low"


def test_context_compiler_accepts_a_wrapped_readonly_tool_output() -> None:
    compiled = NutritionContextCompiler().compile(
        packet(),
        calculation_observations=(
            {
                "observation_id": "tool-receipt-1",
                "calculation_type": "bmi",
                "bmi": 23.8,
                "category": "reference_range",
                "status": "calculated",
            },
        ),
    )

    observation = compiled.calculation_observations[0]
    assert observation.value == 23.8
    assert observation.details == {
        "bmi": 23.8,
        "category": "reference_range",
        "status": "calculated",
    }


def test_validator_accepts_real_evidence_and_deterministic_calculation_refs() -> None:
    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=valid_assessment(),
    )

    assert report.is_valid


def test_validator_rejects_unknown_evidence_and_missing_calculation_observation() -> None:
    invalid = ProfessionalAssessment(
        assessment_type="progress",
        overall="无依据判断",
        findings=(
            ProfessionalClaim(
                claim_id="claim-1",
                category="trend",
                statement="趋势下降",
                basis_types=("deterministic_calculation",),
                evidence_refs=("forged-evidence",),
                confidence="high",
            ),
        ),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=invalid,
    )

    assert set(report.issue_codes) == {
        NutritionValidationIssueCode.UNKNOWN_EVIDENCE,
        NutritionValidationIssueCode.CALCULATION_EVIDENCE_MISSING,
    }


def test_validator_does_not_allow_visual_uncertainty_to_be_upgraded() -> None:
    visual_claim = ProfessionalAssessment(
        assessment_type="meal",
        overall="图片观察",
        findings=(
            ProfessionalClaim(
                claim_id="claim-visual",
                category="vegetables",
                statement="蔬菜偏少",
                basis_types=("visual_observation",),
                evidence_refs=("visual-1",),
                confidence="high",
            ),
        ),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(visual=True),
        assessment=visual_claim,
    )

    assert NutritionValidationIssueCode.VISUAL_CONFIDENCE_UPGRADED in report.issue_codes


def test_validator_rejects_medical_claim_based_only_on_model_prior() -> None:
    medical = ProfessionalAssessment(
        assessment_type="general",
        overall="未经证据支持的医学判断",
        findings=(
            ProfessionalClaim(
                claim_id="claim-medical",
                category="medical_diagnosis",
                statement="这是未经证据支持的医学判断",
                basis_types=("model_prior",),
                confidence="low",
            ),
        ),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=medical,
    )

    assert NutritionValidationIssueCode.HIGH_RISK_MODEL_PRIOR_ONLY in report.issue_codes


def test_validator_allows_low_risk_meal_observation_with_model_prior() -> None:
    meal_observation = ProfessionalAssessment(
        assessment_type="meal",
        overall="这顿搭配整体比较均衡。",
        findings=(
            ProfessionalClaim(
                claim_id="claim-meal-balance",
                category="meal_balance",
                statement="这顿搭配整体比较均衡。",
                basis_types=("user_evidence", "model_prior"),
                evidence_refs=("weight-1",),
                confidence="medium",
            ),
        ),
        uncertainty_note="这是基于通用营养常识的粗略判断，不替代个体化建议。",
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=meal_observation,
    )

    assert report.is_valid


def test_validator_rejects_medical_model_prior_mixed_with_user_evidence() -> None:
    medical = ProfessionalAssessment(
        assessment_type="general",
        overall="未经专业依据的医学判断",
        findings=(
            ProfessionalClaim(
                claim_id="claim-medical-mixed",
                category="medical_diagnosis",
                statement="这说明用户患有某种疾病。",
                basis_types=("user_evidence", "model_prior"),
                evidence_refs=("weight-1",),
                confidence="low",
            ),
        ),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=medical,
    )

    assert NutritionValidationIssueCode.HIGH_RISK_MODEL_PRIOR_ONLY in report.issue_codes


def test_validator_rejects_citations_when_corpus_is_empty() -> None:
    citation = KnowledgeCitation(
        citation_id="citation-forged",
        source_id="source-forged",
        chunk_id="chunk-forged",
        title="不存在的资料",
        publisher="未知",
        version="1",
        review_status="approved",
        retrieved_in_invocation_id="nutrition-invocation-1",
    )
    invalid = ProfessionalAssessment(
        assessment_type="general",
        overall="无来源知识判断",
        findings=(
            ProfessionalClaim(
                claim_id="claim-rag",
                category="knowledge",
                statement="这是资料结论",
                basis_types=("rag_evidence",),
                knowledge_refs=("citation-forged",),
                confidence="medium",
            ),
        ),
        citations=(citation,),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=context(),
        assessment=invalid,
    )

    assert NutritionValidationIssueCode.EMPTY_CORPUS_CITED in report.issue_codes
    assert NutritionValidationIssueCode.UNKNOWN_CITATION in report.issue_codes


def test_validator_accepts_an_approved_citation_from_the_same_invocation() -> None:
    candidate = KnowledgeCandidate.create(
        candidate_id="candidate-1",
        citation_id="citation-1",
        source_id="source-1",
        chunk_id="chunk-1",
        title="膳食指南",
        publisher="测试机构",
        version="2026",
        review_status="approved",
        active=True,
        content="已审核的体重管理资料内容。",
        adoption_status="adopted",
    )
    retrieval = bind_candidates("nutrition-invocation-1", (candidate,))
    citation = retrieval.citations[0]
    compiled = NutritionContextCompiler().compile(
        packet(),
        knowledge=retrieval,
    )
    cited = ProfessionalAssessment(
        assessment_type="general",
        overall="引用已审核资料",
        findings=(
            ProfessionalClaim(
                claim_id="claim-rag",
                category="knowledge",
                statement="这是资料支持的结论",
                basis_types=("rag_evidence",),
                knowledge_refs=("citation-1",),
                confidence="medium",
            ),
        ),
        citations=(citation,),
    )

    report = NutritionAssessmentValidator().validate(
        invocation_id="nutrition-invocation-1",
        context=compiled,
        assessment=cited,
    )

    assert report.is_valid


async def test_nutrition_agent_returns_valid_assessment_without_tools() -> None:
    gateway = ScriptedModelGateway((model_response(valid_assessment()),))
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context())

    assert result.status.value == "succeeded"
    assert result.assessment == valid_assessment()
    assert result.used_fallback is False
    assert result.repair_attempted is False
    assert result.model_call_count == 1
    assert gateway.requests[0].purpose.value == "nutrition"
    assert gateway.requests[0].messages[0].content == NUTRITION_AGENT_PROMPT
    assert gateway.requests[0].tools == ()
    assert gateway.requests[0].metadata["corpus_status"] == "empty"


async def test_nutrition_specialist_freezes_rag_release_for_its_invocation() -> None:
    snapshot = NutritionRuntimeSnapshot(
        corpus_release_id="release-3",
        corpus_release_version="nutrition_v3",
        corpus_manifest_sha256="a" * 64,
        retrieval_profile_id="retrieval-profile-1",
        embedding_profile_id="embedding-profile-1",
        lexical_profile_id="lexical-profile-1",
        chunker_profile_id="chunker-profile-1",
    )

    class GovernedKnowledgeRepository:
        def __init__(self) -> None:
            self.invocation_id: str | None = None
            self.release_id: str | None = None

        async def get_runtime_snapshot(self) -> NutritionRuntimeSnapshot:
            return snapshot

        async def search(
            self,
            *,
            query: str,
            max_results: int,
            retrieved_in_invocation_id: str | None = None,
            release_id: str | None = None,
        ) -> dict[str, object]:
            assert query == "减脂期间晚餐怎么搭配？"
            assert max_results == 5
            self.invocation_id = retrieved_in_invocation_id
            self.release_id = release_id
            return {
                "corpus_status": "available",
                "candidates": [],
                "citations": [],
                "query_summary": "adopted=0",
            }

        async def get_source(
            self,
            *,
            source_id: str,
            chunk_id: str | None = None,
        ) -> dict[str, object]:
            return {"source_id": source_id, "chunk_id": chunk_id}

    repository = GovernedKnowledgeRepository()
    assessment = ProfessionalAssessment(
        assessment_type="general",
        overall="先保证晚餐结构规律，再根据实际饥饿感调整。",
    )
    specialist = NutritionSpecialist(
        model=ScriptedModelGateway((model_response(assessment),)),
        model_name="fake-nutrition-model",
        graph_version="core-primary-v1",
        nutrition_tools=NutritionToolRegistry(repository),
    )

    result = await specialist.consult(
        NutritionConsultationRequest(
            trace_id="trace-1",
            user_id="user-1",
            thread_id="thread-1",
            turn_id="turn-1",
            parent_invocation_id="core-invocation-1",
            user_request="晚餐不知道怎么吃",
            professional_question="减脂期间晚餐怎么搭配？",
            deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        )
    )

    assert repository.invocation_id == result.invocation.invocation_id
    assert repository.release_id == "release-3"
    assert result.invocation.payload["knowledge_snapshot"] == snapshot.model_dump(mode="json")
    assert result.inputs_artifact.payload["knowledge_snapshot"] == snapshot.model_dump(mode="json")


async def test_nutrition_agent_repairs_visual_confidence_once() -> None:
    invalid = ProfessionalAssessment(
        assessment_type="meal",
        overall="图片看起来蔬菜偏少",
        findings=(
            ProfessionalClaim(
                claim_id="claim-visual",
                category="vegetables",
                statement="图片看起来蔬菜偏少",
                basis_types=("visual_observation",),
                evidence_refs=("visual-1",),
                confidence="high",
            ),
        ),
        uncertainty_note="图片较模糊。",
    )
    repaired = invalid.model_copy(
        update={
            "findings": (invalid.findings[0].model_copy(update={"confidence": Confidence.LOW}),)
        }
    )
    gateway = ScriptedModelGateway(
        (model_response(invalid, tokens=30), model_response(repaired, tokens=40))
    )
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context(visual=True))

    assert result.status.value == "succeeded"
    assert result.repair_attempted is True
    assert result.model_call_count == 2
    assert result.total_token_count == 70
    assert result.assessment.findings[0].confidence.value == "low"
    assert "visual_confidence_upgraded" in (gateway.requests[1].messages[-1].content or "")


async def test_nutrition_agent_repairs_invalid_schema_once() -> None:
    malformed = ModelResponse(message=ModelMessage(role=MessageRole.ASSISTANT, content="not-json"))
    gateway = ScriptedModelGateway((malformed, model_response(valid_assessment())))
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context())

    assert result.status.value == "succeeded"
    assert result.repair_attempted is True
    assert result.model_call_count == 2


async def test_nutrition_agent_degrades_after_second_integrity_failure() -> None:
    invalid = ProfessionalAssessment(
        assessment_type="general",
        overall="没有证据",
        findings=(
            ProfessionalClaim(
                claim_id="claim-forged",
                category="trend",
                statement="趋势下降",
                basis_types=("user_evidence",),
                evidence_refs=("forged",),
                confidence="high",
            ),
        ),
    )
    gateway = ScriptedModelGateway((model_response(invalid), model_response(invalid)))
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context())

    assert result.status.value == "degraded"
    assert result.used_fallback is True
    assert result.repair_attempted is True
    assert result.failure_code == "nutrition_integrity_invalid_after_repair"
    assert result.assessment == ConservativeNutritionFallback().render(context())
    assert result.assessment.findings == ()


async def test_nutrition_agent_rejects_high_risk_model_prior_only_output() -> None:
    unsafe_json = """{
      "schema_version": "1",
      "assessment_type": "general",
      "overall": "存在明确健康风险",
      "findings": [{
        "claim_id": "claim-risk",
        "category": "medical",
        "statement": "这是未经证据支持的健康判断",
        "basis_types": ["model_prior"],
        "evidence_refs": [],
        "knowledge_refs": [],
        "confidence": "high"
      }],
      "actions": [],
      "questions": [],
      "risk_flags": ["medical-risk"],
      "citations": []
    }"""
    unsafe_response = ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=unsafe_json)
    )
    gateway = ScriptedModelGateway((unsafe_response, unsafe_response))
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context())

    assert result.status.value == "degraded"
    assert result.repair_attempted is True
    assert result.model_call_count == 2
    assert result.assessment.findings == ()
    assert result.assessment.risk_flags == ()


async def test_nutrition_agent_degrades_on_provider_failure_without_retry() -> None:
    gateway = ScriptedModelGateway((ModelTimeoutError("planned timeout"),))
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(invocation=invocation(), context=context())

    assert result.status.value == "degraded"
    assert result.failure_code == "model_gateway_error"
    assert result.model_call_count == 0
    assert len(gateway.requests) == 1
    assert result.assessment.risk_flags == ()


async def test_nutrition_agent_rejects_write_permissions_before_model_call() -> None:
    gateway = ScriptedModelGateway(())
    agent = NutritionAgent(
        runner=InvocationRunner(model=gateway),
        model="fake-nutrition-model",
    )

    result = await agent.run(
        invocation=invocation(allowed_tools=("record_weight",), max_tool_calls=1),
        context=context(),
    )

    assert result.status.value == "degraded"
    assert result.failure_code == "nutrition_tools_not_allowed"
    assert gateway.requests == []
