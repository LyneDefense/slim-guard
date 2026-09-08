"""Evidence-bound nutrition specialist with conservative termination."""

from __future__ import annotations

import json

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.contracts import (
    AgentInvocation,
    AgentRole,
    ClaimBasis,
    Confidence,
    InvocationStatus,
    ProfessionalAssessment,
)
from slim_guard.agents.nutrition.contracts import (
    EvidenceAuthority,
    NutritionAgentResult,
    NutritionContext,
    NutritionEvidence,
    NutritionValidationIssue,
    NutritionValidationIssueCode,
    NutritionValidationReport,
)
from slim_guard.agents.nutrition.knowledge import NutritionCitationValidator
from slim_guard.agents.structured_runner import StructuredAgentRunner, StructuredRunResult
from slim_guard.orchestration.graph import InvocationGrant

_CONFIDENCE_RANK = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}
_HIGH_RISK_CATEGORY_MARKERS = frozenset(
    {"diagnosis", "disease", "dosage", "high_risk", "medical", "medication", "treatment"}
)

DEFAULT_NUTRITION_PROMPT_VERSION = "nutrition-assessment-v1"
NUTRITION_AGENT_ALLOWED_TOOLS: tuple[str, ...] = ()
CONSERVATIVE_ASSESSMENT_TEXT = "当前证据不足，暂时无法形成可靠的专业判断。"
NUTRITION_AGENT_PROMPT = (
    "You are SlimGuard's nutrition assessment specialist. Use only supplied evidence, "
    "deterministic observations, and supplied knowledge citations. Every claim must "
    "reference real evidence; every action must reference a real claim. Keep visual "
    "uncertainty and confidence unchanged or lower. Never invent a citation, diagnosis, "
    "treatment, dose, saved record, or user fact. An empty corpus means there is no "
    "knowledge source to cite. Return only one ProfessionalAssessment JSON object and no "
    "hidden reasoning."
)


class NutritionAssessmentValidator:
    """Prove that every specialist output reference came from its invocation context."""

    def __init__(
        self,
        citation_validator: NutritionCitationValidator | None = None,
    ) -> None:
        self._citation_validator = citation_validator or NutritionCitationValidator()

    def validate(
        self,
        *,
        invocation_id: str,
        context: NutritionContext,
        assessment: ProfessionalAssessment,
    ) -> NutritionValidationReport:
        issues: list[NutritionValidationIssue] = []
        evidence_by_id = {item.evidence_id: item for item in context.evidence}
        calculation_ids = {
            observation.observation_id for observation in context.calculation_observations
        }
        available_ids = set(evidence_by_id) | calculation_ids

        for claim in assessment.findings:
            claim_refs = set(claim.evidence_refs)
            for evidence_id in sorted(claim_refs - available_ids):
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.UNKNOWN_EVIDENCE,
                        f"{claim.claim_id}:{evidence_id}",
                    )
                )

            visual_items = tuple(
                evidence_by_id[reference]
                for reference in claim.evidence_refs
                if reference in evidence_by_id and self._is_visual(evidence_by_id[reference])
            )
            has_visual_basis = ClaimBasis.VISUAL_OBSERVATION in claim.basis_types
            if visual_items and not has_visual_basis:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.VISUAL_BASIS_MISSING,
                        claim.claim_id,
                    )
                )
            if has_visual_basis and not visual_items:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.VISUAL_EVIDENCE_MISSING,
                        claim.claim_id,
                    )
                )
            if visual_items and self._visual_confidence_upgraded(claim.confidence, visual_items):
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.VISUAL_CONFIDENCE_UPGRADED,
                        claim.claim_id,
                    )
                )

            if ClaimBasis.DETERMINISTIC_CALCULATION in claim.basis_types and not (
                claim_refs & calculation_ids
            ):
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CALCULATION_EVIDENCE_MISSING,
                        claim.claim_id,
                    )
                )

            high_risk = bool(assessment.risk_flags) or any(
                marker in claim.category.casefold() for marker in _HIGH_RISK_CATEGORY_MARKERS
            )
            if high_risk and set(claim.basis_types) == {ClaimBasis.MODEL_PRIOR}:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.HIGH_RISK_MODEL_PRIOR_ONLY,
                        claim.claim_id,
                    )
                )

        citation_report = self._citation_validator.validate(
            invocation_id=invocation_id,
            context=context,
            assessment=assessment,
        )
        issues.extend(citation_report.issues)

        return NutritionValidationReport(issues=tuple(issues))

    @staticmethod
    def _is_visual(evidence: NutritionEvidence) -> bool:
        source_type = evidence.source_type.casefold()
        return evidence.authority is EvidenceAuthority.OBSERVATION and any(
            marker in source_type for marker in ("visual", "vision", "image", "photo")
        )

    @staticmethod
    def _visual_confidence_upgraded(
        claim_confidence: Confidence,
        evidence: tuple[NutritionEvidence, ...],
    ) -> bool:
        maximums = []
        for item in evidence:
            if item.uncertainty is not None or item.confidence is None:
                maximums.append(_CONFIDENCE_RANK[Confidence.LOW])
            else:
                maximums.append(_CONFIDENCE_RANK[item.confidence])
        return bool(maximums) and _CONFIDENCE_RANK[claim_confidence] > min(maximums)


class ConservativeNutritionFallback:
    """Build a fact-free assessment suitable for every specialist failure."""

    def render(self, context: NutritionContext) -> ProfessionalAssessment:
        return ProfessionalAssessment(
            assessment_type="general",
            overall=CONSERVATIVE_ASSESSMENT_TEXT,
            uncertainty_note="需要基于更完整、可核对的信息再继续分析。",
        )


class NutritionAgent:
    """Generate one typed assessment and repair invalid output at most once."""

    def __init__(
        self,
        *,
        runner: StructuredAgentRunner,
        model: str,
        validator: NutritionAssessmentValidator | None = None,
        fallback: ConservativeNutritionFallback | None = None,
        prompt_version: str = DEFAULT_NUTRITION_PROMPT_VERSION,
    ) -> None:
        if not model.strip():
            raise ValueError("Nutrition model cannot be blank")
        if not prompt_version.strip():
            raise ValueError("Nutrition prompt version cannot be blank")
        self._runner = runner
        self._model = model
        self._validator = validator or NutritionAssessmentValidator()
        self._fallback = fallback or ConservativeNutritionFallback()
        self._prompt_version = prompt_version

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        context: NutritionContext,
        grant: InvocationGrant | None = None,
        review_feedback: tuple[str, ...] = (),
    ) -> NutritionAgentResult:
        boundary_failure = self._boundary_failure(invocation, context)
        if boundary_failure is not None:
            return self._degraded(context=context, failure_code=boundary_failure)

        request = self._request(
            invocation=invocation,
            context=context,
            review_feedback=review_feedback,
        )
        responses: list[ModelResponse] = []
        total_tokens = 0
        last_report = NutritionValidationReport()
        last_failure = "nutrition_generation_failed"
        repair_attempted = False

        try:
            first = await self._single_call(
                invocation=invocation,
                request=request,
                grant=grant,
                remaining_tokens=invocation.max_total_tokens,
            )
            responses.extend(first.responses)
            total_tokens += first.total_token_count
            if first.output is not None:
                last_report = self._validator.validate(
                    invocation_id=invocation.invocation_id,
                    context=context,
                    assessment=first.output,
                )
                if last_report.is_valid:
                    return self._success(
                        assessment=first.output,
                        responses=responses,
                        total_tokens=total_tokens,
                        repair_attempted=False,
                    )
                last_failure = "nutrition_integrity_invalid"
            else:
                last_failure = first.failure_code or "nutrition_generation_failed"

            if first.output is None and not self._is_repairable_failure(last_failure):
                return self._degraded(
                    context=context,
                    failure_code=last_failure,
                    responses=responses,
                    total_tokens=total_tokens,
                    validation_report=last_report,
                )

            remaining_calls = invocation.max_model_calls - len(responses)
            remaining_tokens = invocation.max_total_tokens - total_tokens
            if remaining_calls <= 0 or remaining_tokens <= 0:
                return self._degraded(
                    context=context,
                    failure_code=last_failure,
                    responses=responses,
                    total_tokens=total_tokens,
                    validation_report=last_report,
                )

            repair_attempted = True
            repaired = await self._single_call(
                invocation=invocation,
                request=self._repair_request(
                    request=request,
                    previous=responses[-1] if responses else None,
                    validation_report=last_report,
                ),
                grant=grant,
                remaining_tokens=remaining_tokens,
            )
            responses.extend(repaired.responses)
            total_tokens += repaired.total_token_count
            if repaired.output is not None:
                last_report = self._validator.validate(
                    invocation_id=invocation.invocation_id,
                    context=context,
                    assessment=repaired.output,
                )
                if last_report.is_valid:
                    return self._success(
                        assessment=repaired.output,
                        responses=responses,
                        total_tokens=total_tokens,
                        repair_attempted=True,
                    )
                last_failure = "nutrition_integrity_invalid_after_repair"
            else:
                last_failure = repaired.failure_code or "nutrition_repair_failed"
        except Exception:
            last_failure = "nutrition_internal_error"

        return self._degraded(
            context=context,
            failure_code=last_failure,
            responses=responses,
            total_tokens=total_tokens,
            repair_attempted=repair_attempted,
            validation_report=last_report,
        )

    async def _single_call(
        self,
        *,
        invocation: AgentInvocation,
        request: ModelRequest,
        grant: InvocationGrant | None,
        remaining_tokens: int,
    ) -> StructuredRunResult[ProfessionalAssessment]:
        single_call_invocation = invocation.model_copy(
            update={"max_model_calls": 1, "max_total_tokens": remaining_tokens}
        )
        return await self._runner.run(
            invocation=single_call_invocation,
            request=request.model_copy(
                update={"max_output_tokens": min(request.max_output_tokens, remaining_tokens)}
            ),
            output_type=ProfessionalAssessment,
            grant=grant,
        )

    def _request(
        self,
        *,
        invocation: AgentInvocation,
        context: NutritionContext,
        review_feedback: tuple[str, ...] = (),
    ) -> ModelRequest:
        system = ModelMessage(
            role=MessageRole.SYSTEM,
            content=NUTRITION_AGENT_PROMPT,
        )
        return ModelRequest(
            purpose=ModelPurpose.NUTRITION,
            model=self._model,
            messages=(
                system,
                ModelMessage(
                    role=MessageRole.USER,
                    content=self._model_context_json(
                        context,
                        review_feedback=review_feedback,
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=ProfessionalAssessment.__name__,
            max_output_tokens=min(3072, invocation.max_total_tokens),
            temperature=0,
            metadata={
                "invocation_id": invocation.invocation_id,
                "prompt_version": self._prompt_version,
                "corpus_status": context.knowledge.corpus_status.value,
            },
        )

    @staticmethod
    def _model_context_json(
        context: NutritionContext,
        *,
        review_feedback: tuple[str, ...] = (),
    ) -> str:
        """Do not expose rejected draft, inactive, or inapplicable candidate text."""

        payload = context.model_dump(mode="json")
        knowledge = payload["knowledge"]
        allowed_ids = {
            citation.citation_id for citation in context.knowledge.citations
        }
        knowledge["candidates"] = [
            candidate.model_dump(mode="json")
            for candidate in context.knowledge.candidates
            if candidate.citation_id in allowed_ids
        ]
        if review_feedback:
            payload = {
                "nutrition_context": payload,
                "review_feedback_issue_types": list(review_feedback),
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _repair_request(
        *,
        request: ModelRequest,
        previous: ModelResponse | None,
        validation_report: NutritionValidationReport,
    ) -> ModelRequest:
        messages = list(request.messages)
        if previous is not None:
            messages.append(previous.message)
        issue_codes = validation_report.issue_codes or ("structured_output_invalid",)
        messages.append(
            ModelMessage(
                role=MessageRole.USER,
                content=(
                    "Repair the prior output once. Return only a complete "
                    "ProfessionalAssessment JSON object. Do not add any evidence, claim, "
                    "citation, or certainty. Validation issue codes: "
                    + json.dumps(issue_codes, ensure_ascii=False)
                ),
            )
        )
        return request.model_copy(update={"messages": tuple(messages)})

    @staticmethod
    def _boundary_failure(invocation: AgentInvocation, context: NutritionContext) -> str | None:
        if invocation.agent_role is not AgentRole.NUTRITION_EXPERT:
            return "nutrition_invocation_role_mismatch"
        if invocation.turn_id != context.turn_id:
            return "nutrition_context_turn_mismatch"
        if (
            invocation.allowed_tools != NUTRITION_AGENT_ALLOWED_TOOLS
            or invocation.max_tool_calls != 0
        ):
            return "nutrition_tools_not_allowed"
        return None

    @staticmethod
    def _is_repairable_failure(failure_code: str) -> bool:
        return failure_code in {
            "structured_output_invalid",
            "structured_output_missing",
        }

    @staticmethod
    def _success(
        *,
        assessment: ProfessionalAssessment,
        responses: list[ModelResponse],
        total_tokens: int,
        repair_attempted: bool,
    ) -> NutritionAgentResult:
        return NutritionAgentResult(
            status=InvocationStatus.SUCCEEDED,
            assessment=assessment,
            model_responses=tuple(responses),
            model_call_count=len(responses),
            total_token_count=total_tokens,
            used_fallback=False,
            repair_attempted=repair_attempted,
        )

    def _degraded(
        self,
        *,
        context: NutritionContext,
        failure_code: str,
        responses: list[ModelResponse] | None = None,
        total_tokens: int = 0,
        repair_attempted: bool = False,
        validation_report: NutritionValidationReport | None = None,
    ) -> NutritionAgentResult:
        model_responses = tuple(responses or ())
        return NutritionAgentResult(
            status=InvocationStatus.DEGRADED,
            assessment=self._fallback.render(context),
            model_responses=model_responses,
            model_call_count=len(model_responses),
            total_token_count=total_tokens,
            used_fallback=True,
            repair_attempted=repair_attempted,
            failure_code=failure_code,
            validation_report=validation_report or NutritionValidationReport(),
        )


__all__ = [
    "CONSERVATIVE_ASSESSMENT_TEXT",
    "ConservativeNutritionFallback",
    "DEFAULT_NUTRITION_PROMPT_VERSION",
    "NUTRITION_AGENT_ALLOWED_TOOLS",
    "NUTRITION_AGENT_PROMPT",
    "NutritionAgent",
    "NutritionAssessmentValidator",
]
