"""Typed, minimal inputs and safe results for the nutrition specialist."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field, HttpUrl, field_validator, model_validator

from slim_guard.agent_models.gateway import ModelResponse
from slim_guard.agents.contracts import (
    Confidence,
    ContractModel,
    InvocationStatus,
    KnowledgeCitation,
    KnowledgeReviewStatus,
    ProfessionalAssessment,
)


class EvidenceAuthority(StrEnum):
    AUTHORITATIVE = "authoritative"
    USER_REPORTED = "user_reported"
    OBSERVATION = "observation"


class KnowledgeCorpusStatus(StrEnum):
    EMPTY = "empty"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class KnowledgeAdoptionStatus(StrEnum):
    """Repository ranking disposition; only selected values may reach the model."""

    ADOPTED = "adopted"
    SELECTED = "selected"
    CANDIDATE_ONLY = "candidate_only"
    CANDIDATE = "candidate"


class KnowledgeCandidate(ContractModel):
    """Unbound, content-verified repository result; never authored by the model."""

    candidate_id: str = Field(min_length=1, max_length=128)
    citation_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    chunk_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    published_at: date | None = None
    version: str = Field(min_length=1, max_length=128)
    section_or_page: str | None = Field(default=None, min_length=1, max_length=512)
    source_url: HttpUrl | None = None
    applicability: tuple[str, ...] = Field(default=(), max_length=32)
    review_status: KnowledgeReviewStatus
    active: bool
    content: str = Field(min_length=1, max_length=16_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rank: int | None = Field(default=None, ge=1, le=10_000, strict=True)
    keyword_score: float | None = Field(default=None, ge=0)
    vector_score: float | None = Field(default=None, ge=0)
    rerank_score: float | None = Field(default=None, ge=0)
    match_reasons: tuple[str, ...] = Field(default=(), max_length=32)
    adoption_status: KnowledgeAdoptionStatus = KnowledgeAdoptionStatus.CANDIDATE_ONLY

    @field_validator("applicability", "match_reasons")
    @classmethod
    def validate_applicability(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Knowledge candidate list values cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Knowledge candidate list values must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_content_hash(self) -> KnowledgeCandidate:
        expected = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected, self.content_sha256):
            raise ValueError("Knowledge candidate content hash does not match")
        return self

    @classmethod
    def create(cls, **values: Any) -> KnowledgeCandidate:
        content = values.get("content")
        if not isinstance(content, str):
            raise ValueError("Knowledge candidate content must be a string")
        values.pop("content_sha256", None)
        return cls(
            **values,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def bind(self, invocation_id: str) -> KnowledgeCitation:
        """Create trusted invocation provenance; repository values cannot override it."""

        return KnowledgeCitation(
            citation_id=self.citation_id,
            source_id=self.source_id,
            chunk_id=self.chunk_id,
            title=self.title,
            publisher=self.publisher,
            published_at=self.published_at,
            version=self.version,
            section_or_page=self.section_or_page,
            source_url=self.source_url,
            applicability=self.applicability,
            review_status=self.review_status,
            retrieved_in_invocation_id=invocation_id,
        )

    @property
    def selected_for_adoption(self) -> bool:
        return self.adoption_status in {
            KnowledgeAdoptionStatus.ADOPTED,
            KnowledgeAdoptionStatus.SELECTED,
        }


class CandidateRejectionReason(StrEnum):
    INACTIVE = "inactive"
    NOT_APPROVED = "not_approved"
    INAPPLICABLE = "inapplicable"
    NOT_SELECTED = "not_selected"


class RejectedKnowledgeCandidate(ContractModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    reason: CandidateRejectionReason


class EvidencePacketLike(Protocol):
    @property
    def turn_id(self) -> str: ...

    @property
    def user_request(self) -> str: ...

    @property
    def professional_question(self) -> str: ...

    @property
    def items(self) -> Sequence[object]: ...

    @property
    def missing_information(self) -> Sequence[str]: ...


class NutritionEvidence(ContractModel):
    """A privacy-minimized projection of one real evidence item."""

    evidence_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=128)
    authority: EvidenceAuthority
    content: dict[str, Any]
    confidence: Confidence | None = None
    uncertainty: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_visual_provenance(self) -> NutritionEvidence:
        source_type = self.source_type.casefold()
        is_visual = any(
            marker in source_type for marker in ("visual", "vision", "image", "photo")
        )
        if is_visual and self.authority is not EvidenceAuthority.OBSERVATION:
            raise ValueError("Visual evidence must retain observation authority")
        if is_visual and (self.confidence is None or self.uncertainty is None):
            raise ValueError("Visual evidence requires confidence and explicit uncertainty")
        return self


class CalculationObservation(ContractModel):
    """A precomputed, read-only deterministic observation supplied by code."""

    observation_id: str = Field(min_length=1, max_length=128)
    calculation_type: str = Field(min_length=1, max_length=128)
    value: int | float | str | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def reject_boolean_value(
        cls,
        value: int | float | str | None,
    ) -> int | float | str | None:
        if isinstance(value, bool):
            raise ValueError("Calculation values cannot be booleans")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Calculation values cannot be blank")
        return value

    @model_validator(mode="after")
    def require_result(self) -> CalculationObservation:
        if self.value is None and not self.details:
            raise ValueError("A calculation observation requires a value or result details")
        return self


class KnowledgeRetrieval(ContractModel):
    corpus_status: KnowledgeCorpusStatus
    required_applicability: tuple[str, ...] = Field(default=(), max_length=32)
    candidates: tuple[KnowledgeCandidate, ...] = Field(default=(), max_length=128)
    citations: tuple[KnowledgeCitation, ...] = Field(default=(), max_length=128)
    rejected_candidates: tuple[RejectedKnowledgeCandidate, ...] = Field(
        default=(),
        max_length=128,
    )
    query_summary: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> KnowledgeRetrieval:
        if any(not item.strip() for item in self.required_applicability):
            raise ValueError("Required applicability cannot contain blank values")
        if len(self.required_applicability) != len(set(self.required_applicability)):
            raise ValueError("Required applicability values must be unique")
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("Knowledge retrieval citation IDs must be unique")
        candidate_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Knowledge retrieval candidate IDs must be unique")
        candidate_citation_ids = tuple(
            candidate.citation_id for candidate in self.candidates
        )
        if len(candidate_citation_ids) != len(set(candidate_citation_ids)):
            raise ValueError("Knowledge candidate citation IDs must be unique")
        rejected_ids = tuple(item.candidate_id for item in self.rejected_candidates)
        if len(rejected_ids) != len(set(rejected_ids)):
            raise ValueError("Rejected candidate IDs must be unique")
        if self.corpus_status is KnowledgeCorpusStatus.EMPTY and (
            self.candidates or self.citations or self.rejected_candidates
        ):
            raise ValueError("An empty knowledge corpus cannot contain candidates")
        if self.corpus_status in {
            KnowledgeCorpusStatus.UNAVAILABLE,
            KnowledgeCorpusStatus.ERROR,
        } and (self.candidates or self.citations or self.rejected_candidates):
            raise ValueError("An unavailable knowledge result cannot contain candidates")
        if not set(citation_ids).issubset(candidate_citation_ids):
            raise ValueError("Bound citations must come from retrieved candidates")
        rejection_by_candidate_id = {
            rejection.candidate_id: rejection.reason
            for rejection in self.rejected_candidates
        }
        eligible_candidate_ids = {
            candidate.candidate_id
            for candidate in self.candidates
            if candidate.citation_id in citation_ids
        }
        if set(rejection_by_candidate_id) != set(candidate_ids).difference(
            eligible_candidate_ids
        ):
            raise ValueError("Every ineligible candidate requires one rejection reason")
        for candidate in self.candidates:
            rejection_reason: CandidateRejectionReason | None = None
            if not candidate.active:
                rejection_reason = CandidateRejectionReason.INACTIVE
            elif candidate.review_status is not KnowledgeReviewStatus.APPROVED:
                rejection_reason = CandidateRejectionReason.NOT_APPROVED
            elif not set(self.required_applicability).issubset(
                candidate.applicability
            ):
                rejection_reason = CandidateRejectionReason.INAPPLICABLE
            elif not candidate.selected_for_adoption:
                rejection_reason = CandidateRejectionReason.NOT_SELECTED

            if candidate.citation_id not in citation_ids:
                if rejection_by_candidate_id[candidate.candidate_id] is not rejection_reason:
                    raise ValueError("Knowledge candidate rejection reason is incorrect")
                continue
            if rejection_reason is not None:
                raise ValueError("Bound knowledge citations must pass every eligibility rule")
            citation = next(
                item for item in self.citations if item.citation_id == candidate.citation_id
            )
            expected = candidate.bind(citation.retrieved_in_invocation_id)
            if expected.model_dump(mode="json") != citation.model_dump(mode="json"):
                raise ValueError("Bound citation metadata does not match its candidate")
        return self

    @classmethod
    def empty(cls) -> KnowledgeRetrieval:
        return cls(corpus_status=KnowledgeCorpusStatus.EMPTY)


class NutritionContext(ContractModel):
    """The complete and only context visible to the nutrition model."""

    schema_version: Literal["1"] = "1"
    turn_id: str = Field(min_length=1, max_length=128)
    user_request: str = Field(min_length=1, max_length=4000)
    professional_question: str = Field(min_length=1, max_length=2000)
    evidence: tuple[NutritionEvidence, ...] = Field(default=(), max_length=64)
    missing_information: tuple[str, ...] = Field(default=(), max_length=32)
    calculation_observations: tuple[CalculationObservation, ...] = Field(
        default=(),
        max_length=32,
    )
    knowledge: KnowledgeRetrieval = Field(default_factory=KnowledgeRetrieval.empty)

    @model_validator(mode="after")
    def validate_references(self) -> NutritionContext:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        calculation_ids = tuple(
            observation.observation_id for observation in self.calculation_observations
        )
        all_ids = (*evidence_ids, *calculation_ids)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Nutrition context evidence and observation IDs must be unique")
        if any(not item.strip() for item in self.missing_information):
            raise ValueError("Missing-information entries cannot be blank")
        if len(self.missing_information) != len(set(self.missing_information)):
            raise ValueError("Missing-information entries must be unique")
        return self

    @property
    def available_evidence_ids(self) -> frozenset[str]:
        return frozenset(
            [item.evidence_id for item in self.evidence]
            + [item.observation_id for item in self.calculation_observations]
        )


class NutritionValidationIssueCode(StrEnum):
    UNKNOWN_EVIDENCE = "unknown_evidence"
    VISUAL_BASIS_MISSING = "visual_basis_missing"
    VISUAL_EVIDENCE_MISSING = "visual_evidence_missing"
    VISUAL_CONFIDENCE_UPGRADED = "visual_confidence_upgraded"
    CALCULATION_EVIDENCE_MISSING = "calculation_evidence_missing"
    UNKNOWN_CITATION = "unknown_citation"
    CITATION_CHANGED = "citation_changed"
    CITATION_NOT_APPROVED = "citation_not_approved"
    CITATION_INVOCATION_MISMATCH = "citation_invocation_mismatch"
    EMPTY_CORPUS_CITED = "empty_corpus_cited"
    HIGH_RISK_MODEL_PRIOR_ONLY = "high_risk_model_prior_only"
    RAG_CLAIM_UNCOVERED = "rag_claim_uncovered"
    NON_RAG_CITATION = "non_rag_citation"
    UNUSED_CITATION = "unused_citation"
    CITATION_INACTIVE = "citation_inactive"
    CITATION_INAPPLICABLE = "citation_inapplicable"


@dataclass(frozen=True, slots=True)
class NutritionValidationIssue:
    code: NutritionValidationIssueCode
    subject: str


@dataclass(frozen=True, slots=True)
class NutritionValidationReport:
    issues: tuple[NutritionValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code.value for issue in self.issues)


@dataclass(frozen=True, slots=True)
class NutritionAgentResult:
    """A specialist result always contains a conservative usable assessment."""

    status: InvocationStatus
    assessment: ProfessionalAssessment
    model_responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    used_fallback: bool
    repair_attempted: bool
    failure_code: str | None = None
    validation_report: NutritionValidationReport = NutritionValidationReport()


EvidencePacketInput = EvidencePacketLike | Mapping[str, Any]
CalculationInput = CalculationObservation | Mapping[str, Any]
KnowledgeInput = KnowledgeRetrieval | Mapping[str, Any]


__all__ = [
    "CalculationInput",
    "CalculationObservation",
    "EvidenceAuthority",
    "EvidencePacketInput",
    "EvidencePacketLike",
    "CandidateRejectionReason",
    "KnowledgeCandidate",
    "KnowledgeAdoptionStatus",
    "KnowledgeCorpusStatus",
    "KnowledgeInput",
    "KnowledgeRetrieval",
    "NutritionAgentResult",
    "NutritionContext",
    "NutritionEvidence",
    "NutritionValidationIssue",
    "NutritionValidationIssueCode",
    "NutritionValidationReport",
    "RejectedKnowledgeCandidate",
]
