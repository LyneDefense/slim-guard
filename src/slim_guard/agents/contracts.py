"""Strict, immutable contracts exchanged by workflow agents.

The models in this module are deliberately independent from model-provider payloads.
Trusted invocation metadata is supplied by the Turn Harness, while models only produce
the typed business payloads defined below.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    Artifact,
    ArtifactProducerRole,
    ContractModel,
    Invocation,
    InvocationStatus,
    canonical_payload_bytes,
    payload_sha256,
    validate_contract,
)


class CommunicationAct(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    CORRECT = "correct"
    REMIND = "remind"
    ENCOURAGE = "encourage"
    EXPLAIN = "explain"
    ASK = "ask"


# The architecture originally called this field ``voice_act``.  Keep the type
# name available while using the broader product term in new contracts.
VoiceAct = CommunicationAct


class RequestedDetail(StrEnum):
    SHORT = "short"
    NORMAL = "normal"
    DETAILED = "detailed"


class ContentBlockKind(StrEnum):
    FACT = "fact"
    CLAIM = "claim"
    ACTION = "action"
    QUESTION = "question"
    RISK = "risk"
    UNCERTAINTY = "uncertainty"
    SOCIAL_ACT = "social_act"


class ClaimBasis(StrEnum):
    USER_EVIDENCE = "user_evidence"
    VISUAL_OBSERVATION = "visual_observation"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    MODEL_PRIOR = "model_prior"
    RAG_EVIDENCE = "rag_evidence"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AssessmentType(StrEnum):
    MEAL = "meal"
    PROGRESS = "progress"
    BEHAVIOR = "behavior"
    GENERAL = "general"


class KnowledgeReviewStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class ReviewerVerdictStatus(StrEnum):
    PASS = "pass"
    REPAIR = "repair"
    REJECT = "reject"


class ReviewerIssueType(StrEnum):
    # Semantic-review issue types from the architecture.
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CHANGED_UNCERTAINTY = "changed_uncertainty"
    MEDICAL_OVERREACH = "medical_overreach"
    ABUSIVE_TONE = "abusive_tone"
    OMITTED_REQUIRED_CONTENT = "omitted_required_content"
    # Routing issue types frozen by the implementation plan.
    STYLE_DRIFT = "style_drift"
    CHANGED_MEANING = "changed_meaning"
    UNSUPPORTED_PROFESSIONAL_CLAIM = "unsupported_professional_claim"
    MISSING_USER_EVIDENCE = "missing_user_evidence"
    DISH_IDENTITY_STRENGTHENED = "dish_identity_strengthened"
    UNSUPPORTED_DISH_GUIDANCE = "unsupported_dish_guidance"
    UNSUPPORTED_AVOIDANCE = "unsupported_avoidance"
    FORBIDDEN_NUTRITION_ESTIMATE = "forbidden_nutrition_estimate"


class RepairTarget(StrEnum):
    CORE = "core"
    NUTRITION_EXPERT = "nutrition_expert"
    RESPONSE_STYLE = "response_style"


class ResponseContentBlock(ContractModel):
    block_id: str = Field(min_length=1, max_length=128)
    kind: ContentBlockKind
    text: str = Field(min_length=1, max_length=4000)
    source_refs: tuple[str, ...] = Field(default=(), max_length=128)
    required: bool = True

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Content source references cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Content source references must be unique")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        sourced_kinds = {
            ContentBlockKind.FACT,
            ContentBlockKind.CLAIM,
            ContentBlockKind.ACTION,
            ContentBlockKind.RISK,
            ContentBlockKind.UNCERTAINTY,
        }
        if self.kind in sourced_kinds and not self.source_refs:
            raise ValueError(f"{self.kind.value} content requires source_refs")
        return self


ContentBlock = ResponseContentBlock
ImmutableContentBlock = ResponseContentBlock


class ResponsePlan(ContractModel):
    schema_version: Literal["1"] = "1"
    communication_act: CommunicationAct
    requested_detail: RequestedDetail = RequestedDetail.NORMAL
    content_blocks: tuple[ResponseContentBlock, ...] = Field(min_length=1, max_length=64)
    citation_refs: tuple[str, ...] = Field(default=(), max_length=128)
    prohibited_transformations: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        block_ids = tuple(block.block_id for block in self.content_blocks)
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("Response plan block IDs must be unique")
        if len(self.citation_refs) != len(set(self.citation_refs)):
            raise ValueError("Response plan citation references must be unique")
        if any(not item.strip() for item in self.citation_refs):
            raise ValueError("Response plan citation references cannot be blank")
        if len(self.prohibited_transformations) != len(set(self.prohibited_transformations)):
            raise ValueError("Prohibited transformations must be unique")
        return self


class KnowledgeCitation(ContractModel):
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
    retrieved_in_invocation_id: str = Field(min_length=1, max_length=128)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    corpus_release_id: str | None = Field(default=None, min_length=1, max_length=128)
    corpus_release_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retrieval_run_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("applicability")
    @classmethod
    def validate_applicability(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Citation applicability cannot contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("Citation applicability values must be unique")
        return value


class ProfessionalClaim(ContractModel):
    claim_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    basis_types: tuple[ClaimBasis, ...] = Field(min_length=1, max_length=5)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=128)
    knowledge_refs: tuple[str, ...] = Field(default=(), max_length=128)
    confidence: Confidence

    @model_validator(mode="after")
    def validate_basis(self) -> Self:
        if len(self.basis_types) != len(set(self.basis_types)):
            raise ValueError("Claim basis types must be unique")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Claim evidence references must be unique")
        if len(self.knowledge_refs) != len(set(self.knowledge_refs)):
            raise ValueError("Claim knowledge references must be unique")
        if ClaimBasis.RAG_EVIDENCE in self.basis_types and not self.knowledge_refs:
            raise ValueError("A rag_evidence claim requires knowledge_refs")
        evidence_bases = {
            ClaimBasis.USER_EVIDENCE,
            ClaimBasis.VISUAL_OBSERVATION,
            ClaimBasis.DETERMINISTIC_CALCULATION,
        }
        if evidence_bases.intersection(self.basis_types) and not self.evidence_refs:
            raise ValueError("An evidence-based claim requires evidence_refs")
        return self


# The architecture uses both terms; they are intentionally the same contract.
ProfessionalFinding = ProfessionalClaim


class ProfessionalAction(ContractModel):
    action_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    basis_claim_ids: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("basis_claim_ids")
    @classmethod
    def validate_basis_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Action claim references must be unique")
        return value


class ProfessionalAssessment(ContractModel):
    schema_version: Literal["1"] = "1"
    assessment_type: AssessmentType
    overall: str = Field(min_length=1, max_length=4000)
    findings: tuple[ProfessionalClaim, ...] = Field(default=(), max_length=64)
    priority_problem: str | None = Field(default=None, min_length=1, max_length=2000)
    actions: tuple[ProfessionalAction, ...] = Field(default=(), max_length=32)
    questions: tuple[str, ...] = Field(default=(), max_length=16)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=32)
    uncertainty_note: str | None = Field(default=None, min_length=1, max_length=2000)
    citations: tuple[KnowledgeCitation, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        claim_ids = tuple(finding.claim_id for finding in self.findings)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Professional assessment claim IDs must be unique")
        action_ids = tuple(action.action_id for action in self.actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Professional assessment action IDs must be unique")
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("Professional assessment citation IDs must be unique")

        known_claims = set(claim_ids)
        unknown_claims = {
            claim_id
            for action in self.actions
            for claim_id in action.basis_claim_ids
            if claim_id not in known_claims
        }
        if unknown_claims:
            raise ValueError(
                "Actions reference unknown claims: " + ", ".join(sorted(unknown_claims))
            )

        # Support both the new citation ID and the knowledge chunk ID used by the
        # earlier architecture draft, while still proving the reference exists.
        known_knowledge_refs = set(citation_ids)
        known_knowledge_refs.update(citation.chunk_id for citation in self.citations)
        unknown_knowledge = {
            reference
            for finding in self.findings
            for reference in finding.knowledge_refs
            if reference not in known_knowledge_refs
        }
        if unknown_knowledge:
            raise ValueError(
                "Claims reference unknown knowledge citations: "
                + ", ".join(sorted(unknown_knowledge))
            )

        if self.risk_flags:
            for finding in self.findings:
                if set(finding.basis_types) == {ClaimBasis.MODEL_PRIOR}:
                    raise ValueError("Risk-bearing assessments cannot rely only on model_prior")
        return self

    def validate_evidence_ids(self, available_evidence_ids: set[str]) -> None:
        missing = {
            reference
            for finding in self.findings
            for reference in finding.evidence_refs
            if reference not in available_evidence_ids
        }
        if missing:
            raise ValueError("Claims reference unknown evidence: " + ", ".join(sorted(missing)))

    def validate_citation_invocation(self, invocation_id: str) -> None:
        foreign = tuple(
            citation.citation_id
            for citation in self.citations
            if citation.retrieved_in_invocation_id != invocation_id
        )
        if foreign:
            raise ValueError(
                "Citations were retrieved by another invocation: " + ", ".join(foreign)
            )


# Concise name used by the implementation plan.
Assessment = ProfessionalAssessment


class StyledResponse(ContractModel):
    schema_version: Literal["1"] = "1"
    text: str = Field(min_length=1, max_length=16_000)
    used_block_ids: tuple[str, ...] = Field(default=(), max_length=64)
    used_claim_ids: tuple[str, ...] = Field(default=(), max_length=64)
    used_action_ids: tuple[str, ...] = Field(default=(), max_length=32)
    preserved_risk_flags: tuple[str, ...] = Field(default=(), max_length=32)
    preserved_citation_refs: tuple[str, ...] = Field(default=(), max_length=128)
    style_profile_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> Self:
        reference_groups = (
            self.used_block_ids,
            self.used_claim_ids,
            self.used_action_ids,
            self.preserved_risk_flags,
            self.preserved_citation_refs,
        )
        if any(len(group) != len(set(group)) for group in reference_groups):
            raise ValueError("Styled response references must be unique")
        return self

    def validate_against_plan(self, plan: ResponsePlan) -> None:
        block_ids = {block.block_id for block in plan.content_blocks}
        unknown = set(self.used_block_ids).difference(block_ids)
        if unknown:
            raise ValueError(
                "Styled response references unknown blocks: " + ", ".join(sorted(unknown))
            )
        required = {block.block_id for block in plan.content_blocks if block.required}
        missing = required.difference(self.used_block_ids)
        if missing:
            raise ValueError(
                "Styled response omitted required blocks: " + ", ".join(sorted(missing))
            )
        missing_citations = set(plan.citation_refs).difference(self.preserved_citation_refs)
        if missing_citations:
            raise ValueError(
                "Styled response omitted citations: " + ", ".join(sorted(missing_citations))
            )

    def validate_against_assessment(self, assessment: ProfessionalAssessment) -> None:
        known_claims = {finding.claim_id for finding in assessment.findings}
        known_actions = {action.action_id for action in assessment.actions}
        if not set(self.used_claim_ids).issubset(known_claims):
            raise ValueError("Styled response references unknown professional claims")
        if not set(self.used_action_ids).issubset(known_actions):
            raise ValueError("Styled response references unknown professional actions")
        if not set(assessment.risk_flags).issubset(self.preserved_risk_flags):
            raise ValueError("Styled response omitted professional risk flags")


class ReviewerIssue(ContractModel):
    type: ReviewerIssueType
    excerpt: str | None = Field(default=None, min_length=1, max_length=1000)
    explanation: str = Field(min_length=1, max_length=2000)


_STYLE_ISSUES = {
    ReviewerIssueType.STYLE_DRIFT,
    ReviewerIssueType.CHANGED_MEANING,
    ReviewerIssueType.CHANGED_UNCERTAINTY,
    ReviewerIssueType.ABUSIVE_TONE,
    ReviewerIssueType.OMITTED_REQUIRED_CONTENT,
    ReviewerIssueType.DISH_IDENTITY_STRENGTHENED,
    ReviewerIssueType.UNSUPPORTED_DISH_GUIDANCE,
    ReviewerIssueType.UNSUPPORTED_AVOIDANCE,
    ReviewerIssueType.FORBIDDEN_NUTRITION_ESTIMATE,
}
_NUTRITION_ISSUES = {
    ReviewerIssueType.UNSUPPORTED_CLAIM,
    ReviewerIssueType.UNSUPPORTED_PROFESSIONAL_CLAIM,
    ReviewerIssueType.MEDICAL_OVERREACH,
    ReviewerIssueType.UNSUPPORTED_DISH_GUIDANCE,
    ReviewerIssueType.UNSUPPORTED_AVOIDANCE,
    ReviewerIssueType.FORBIDDEN_NUTRITION_ESTIMATE,
}


class ReviewerVerdict(ContractModel):
    schema_version: Literal["1"] = "1"
    verdict: ReviewerVerdictStatus
    repair_target: RepairTarget | None = None
    issue_type: ReviewerIssueType | None = None
    reason_summary: str | None = Field(default=None, min_length=1, max_length=2000)
    issues: tuple[ReviewerIssue, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        if self.verdict is ReviewerVerdictStatus.PASS:
            if self.repair_target is not None or self.issue_type is not None or self.issues:
                raise ValueError("A pass verdict cannot contain issues or a repair target")
            return self

        issue_types = {issue.type for issue in self.issues}
        if self.issue_type is not None:
            issue_types.add(self.issue_type)
        if not issue_types:
            raise ValueError("A non-pass verdict requires an issue")
        if self.reason_summary is None and not self.issues:
            raise ValueError("A non-pass verdict requires a reason_summary or issue explanation")

        if self.verdict is ReviewerVerdictStatus.REPAIR:
            if self.repair_target is None:
                raise ValueError("A repair verdict requires repair_target")
            if self.repair_target is RepairTarget.RESPONSE_STYLE and not issue_types.issubset(
                _STYLE_ISSUES
            ):
                raise ValueError("The issue type cannot be repaired by response_style")
            if self.repair_target is RepairTarget.NUTRITION_EXPERT and not issue_types.issubset(
                _NUTRITION_ISSUES
            ):
                raise ValueError("The issue type cannot be repaired by nutrition_expert")
            if self.repair_target is RepairTarget.CORE and issue_types != {
                ReviewerIssueType.MISSING_USER_EVIDENCE
            }:
                raise ValueError("Only missing_user_evidence can return to core")
        elif self.repair_target is not None:
            raise ValueError("A reject verdict cannot contain repair_target")
        return self


__all__ = [
    "AgentArtifact",
    "AgentInvocation",
    "AgentResult",
    "AgentRole",
    "Artifact",
    "ArtifactProducerRole",
    "Assessment",
    "AssessmentType",
    "ClaimBasis",
    "CommunicationAct",
    "Confidence",
    "ContentBlock",
    "ContentBlockKind",
    "ContractModel",
    "ImmutableContentBlock",
    "Invocation",
    "InvocationStatus",
    "KnowledgeCitation",
    "KnowledgeReviewStatus",
    "ProfessionalAction",
    "ProfessionalAssessment",
    "ProfessionalClaim",
    "ProfessionalFinding",
    "RepairTarget",
    "RequestedDetail",
    "ResponseContentBlock",
    "ResponsePlan",
    "ReviewerIssue",
    "ReviewerIssueType",
    "ReviewerVerdict",
    "ReviewerVerdictStatus",
    "StyledResponse",
    "VoiceAct",
    "canonical_payload_bytes",
    "payload_sha256",
    "validate_contract",
]
