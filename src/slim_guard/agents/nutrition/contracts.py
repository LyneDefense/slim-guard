"""Typed, minimal inputs and safe results for the nutrition specialist."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from slim_guard.agent_models.gateway import ModelResponse
from slim_guard.agents.contracts import (
    Confidence,
    ContractModel,
    InvocationStatus,
    KnowledgeCitation,
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
    citations: tuple[KnowledgeCitation, ...] = Field(default=(), max_length=128)
    query_summary: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> KnowledgeRetrieval:
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("Knowledge retrieval citation IDs must be unique")
        if self.corpus_status is KnowledgeCorpusStatus.EMPTY and self.citations:
            raise ValueError("An empty knowledge corpus cannot contain citations")
        if self.corpus_status in {
            KnowledgeCorpusStatus.UNAVAILABLE,
            KnowledgeCorpusStatus.ERROR,
        } and self.citations:
            raise ValueError("An unavailable knowledge result cannot contain citations")
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
    "KnowledgeCorpusStatus",
    "KnowledgeInput",
    "KnowledgeRetrieval",
    "NutritionAgentResult",
    "NutritionContext",
    "NutritionEvidence",
    "NutritionValidationIssue",
    "NutritionValidationIssueCode",
    "NutritionValidationReport",
]
