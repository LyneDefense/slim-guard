"""Strict, immutable contracts exchanged by workflow agents.

The models in this module are deliberately independent from model-provider payloads.
Trusted invocation metadata is supplied by the coordinator, while models only produce
the typed business payloads defined below.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    """Base configuration shared by data crossing an agent boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    NUTRITION_EXPERT = "nutrition_expert"
    RESPONSE_STYLE = "response_style"
    RESPONSE_REVIEWER = "response_reviewer"


class ArtifactProducerRole(StrEnum):
    MEMORY_INGESTION = "memory_ingestion"
    MEMORY_RECALL = "memory_recall"
    ORCHESTRATOR = "orchestrator"
    BUSINESS_TOOL = "business_tool"
    EVIDENCE_BUILDER = "evidence_builder"
    NUTRITION_EXPERT = "nutrition_expert"
    NUTRITION_TOOL = "nutrition_tool"
    STYLE_RESOLVER = "style_resolver"
    RESPONSE_STYLE = "response_style"
    RESPONSE_REVIEWER = "response_reviewer"
    COORDINATOR = "coordinator"


class InvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class ResponsePath(StrEnum):
    DIRECT = "direct"
    PROFESSIONAL_ASSESSMENT = "professional_assessment"
    SAFETY = "safety"


class InteractionKind(StrEnum):
    CHECKIN = "checkin"
    CORRECTION = "correction"
    QUESTION = "question"
    REVIEW = "review"
    REMINDER = "reminder"
    CHAT = "chat"


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


class RepairTarget(StrEnum):
    ORCHESTRATOR = "orchestrator"
    NUTRITION_EXPERT = "nutrition_expert"
    RESPONSE_STYLE = "response_style"


class AgentInvocation(ContractModel):
    """Coordinator-created envelope for one bounded agent attempt."""

    invocation_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=128)
    agent_role: AgentRole = Field(validation_alias=AliasChoices("agent_role", "callee"))
    agent_version: str = Field(min_length=1, max_length=128)
    attempt: int = Field(default=1, ge=1, le=16, strict=True)
    caller: str = Field(default="coordinator", min_length=1, max_length=128)
    parent_invocation_id: str | None = Field(default=None, min_length=1, max_length=128)
    input_artifact_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("input_artifact_ids", "parent_artifact_ids"),
        max_length=128,
    )
    input_schema: str | None = Field(default=None, min_length=1, max_length=128)
    input_schema_version: str = Field(default="1", min_length=1, max_length=32)
    allowed_tools: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("allowed_tools", "allowed_tool_names"),
        max_length=64,
    )
    privacy_scopes: tuple[str, ...] = Field(default=(), max_length=64)
    deadline_at: datetime
    max_model_calls: int = Field(ge=1, le=32, strict=True)
    max_tool_calls: int = Field(ge=0, le=64, strict=True)
    max_total_tokens: int = Field(ge=1, le=10_000_000, strict=True)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("deadline_at")
    @classmethod
    def validate_deadline(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Invocation deadline must be timezone-aware")
        return value

    @field_validator("input_artifact_ids", "allowed_tools", "privacy_scopes")
    @classmethod
    def reject_duplicate_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Invocation references and grants cannot contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("Invocation references and grants must be unique")
        return value

    @property
    def callee(self) -> AgentRole:
        return self.agent_role

    @property
    def parent_artifact_ids(self) -> tuple[str, ...]:
        return self.input_artifact_ids

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return self.allowed_tools


# Short name used in some design notes.
Invocation = AgentInvocation


class AgentResult(ContractModel):
    invocation_id: str = Field(min_length=1, max_length=128)
    status: InvocationStatus
    output_schema: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=32)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_receipt_ids: tuple[str, ...] = Field(default=(), max_length=64)
    model_call_count: int = Field(ge=0, le=32, strict=True)
    tool_call_count: int = Field(ge=0, le=64, strict=True)
    token_usage: int = Field(ge=0, le=10_000_000, strict=True)
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is InvocationStatus.SUCCEEDED:
            if self.artifact_id is None:
                raise ValueError("A succeeded invocation requires an artifact_id")
            if self.failure_code is not None:
                raise ValueError("A succeeded invocation cannot have a failure_code")
        elif self.status is InvocationStatus.FAILED and self.failure_code is None:
            raise ValueError("A failed invocation requires a failure_code")
        return self


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Return the stable JSON representation used for artifact hashes."""

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Artifact payload must contain finite JSON values") from error
    return serialized.encode("utf-8")


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


class AgentArtifact(ContractModel):
    """An immutable, content-address-verified result exchanged between agents."""

    artifact_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    producer_role: ArtifactProducerRole
    artifact_type: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=32)
    parent_artifact_ids: tuple[str, ...] = Field(default=(), max_length=128)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Artifact creation time must be timezone-aware")
        return value

    @field_validator("parent_artifact_ids")
    @classmethod
    def validate_parent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Artifact parent IDs cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Artifact parent IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.artifact_id in self.parent_artifact_ids:
            raise ValueError("An artifact cannot be its own parent")
        expected = payload_sha256(self.payload)
        if not hmac.compare_digest(expected, self.payload_sha256):
            raise ValueError("Artifact payload_sha256 does not match its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        turn_id: str,
        producer_role: ArtifactProducerRole,
        artifact_type: str,
        schema_version: str,
        payload: dict[str, Any],
        created_at: datetime,
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> AgentArtifact:
        return cls(
            artifact_id=artifact_id,
            turn_id=turn_id,
            producer_role=producer_role,
            artifact_type=artifact_type,
            schema_version=schema_version,
            parent_artifact_ids=parent_artifact_ids,
            payload_sha256=payload_sha256(payload),
            payload=payload,
            created_at=created_at,
        )

    def verify_payload(self) -> bool:
        return hmac.compare_digest(payload_sha256(self.payload), self.payload_sha256)


Artifact = AgentArtifact


class TurnDirective(ContractModel):
    schema_version: Literal["1"] = "1"
    response_path: ResponsePath
    interaction_kind: InteractionKind
    user_need_summary: str = Field(min_length=1, max_length=1000)
    response_brief: str = Field(min_length=1, max_length=4000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=128)
    professional_question: str | None = Field(default=None, min_length=1, max_length=2000)
    voice_act: CommunicationAct
    requested_detail: RequestedDetail = RequestedDetail.NORMAL

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Directive evidence references cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Directive evidence references must be unique")
        return value

    @model_validator(mode="after")
    def validate_response_path(self) -> Self:
        if self.response_path is ResponsePath.PROFESSIONAL_ASSESSMENT:
            if self.professional_question is None:
                raise ValueError("Professional assessment requires professional_question")
            if not self.evidence_refs:
                raise ValueError("Professional assessment requires evidence_refs")
        elif self.professional_question is not None:
            raise ValueError("professional_question is only valid for professional assessment")
        return self


Directive = TurnDirective


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
}
_NUTRITION_ISSUES = {
    ReviewerIssueType.UNSUPPORTED_CLAIM,
    ReviewerIssueType.UNSUPPORTED_PROFESSIONAL_CLAIM,
    ReviewerIssueType.MEDICAL_OVERREACH,
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
            if self.repair_target is RepairTarget.ORCHESTRATOR and issue_types != {
                ReviewerIssueType.MISSING_USER_EVIDENCE
            }:
                raise ValueError("Only missing_user_evidence can return to orchestrator")
        elif self.repair_target is not None:
            raise ValueError("A reject verdict cannot contain repair_target")
        return self


def validate_contract(model: type[ContractModel], payload: dict[str, Any]) -> ContractModel:
    """Validate an untrusted model payload without accepting undeclared fields."""

    try:
        return model.model_validate(payload)
    except ValidationError:
        raise


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
    "Directive",
    "ImmutableContentBlock",
    "InteractionKind",
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
    "ResponsePath",
    "ResponsePlan",
    "ReviewerIssue",
    "ReviewerIssueType",
    "ReviewerVerdict",
    "ReviewerVerdictStatus",
    "StyledResponse",
    "TurnDirective",
    "VoiceAct",
    "canonical_payload_bytes",
    "payload_sha256",
    "validate_contract",
]
