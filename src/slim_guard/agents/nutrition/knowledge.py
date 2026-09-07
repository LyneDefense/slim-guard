"""Trusted knowledge-candidate binding and citation validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import Field, field_validator

from slim_guard.agents.contracts import (
    ClaimBasis,
    ContractModel,
    KnowledgeReviewStatus,
    ProfessionalAssessment,
)
from slim_guard.agents.nutrition.contracts import (
    CandidateRejectionReason,
    KnowledgeCandidate,
    KnowledgeCorpusStatus,
    KnowledgeRetrieval,
    NutritionContext,
    NutritionValidationIssue,
    NutritionValidationIssueCode,
    RejectedKnowledgeCandidate,
)


class NutritionKnowledgeRepositoryAdapter(Protocol):
    """Repository boundary accepted by ``NutritionToolRegistry`` unchanged."""

    async def search(self, *, query: str, max_results: int) -> Mapping[str, Any]: ...

    async def get_source(
        self,
        *,
        source_id: str,
        chunk_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class CitationValidationPolicy(ContractModel):
    required_applicability: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("required_applicability")
    @classmethod
    def validate_applicability(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Required applicability cannot contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Required applicability values must be unique")
        return normalized


@dataclass(frozen=True, slots=True)
class CitationValidationReport:
    issues: tuple[NutritionValidationIssue, ...]
    rag_claim_count: int
    covered_rag_claim_count: int

    @property
    def is_valid(self) -> bool:
        return not self.issues and self.coverage_percent == 100.0

    @property
    def coverage_percent(self) -> float:
        if self.rag_claim_count == 0:
            return 100.0
        return self.covered_rag_claim_count / self.rag_claim_count * 100.0


class KnowledgeCandidateBinder:
    """Bind trusted repository candidates to exactly one Nutrition invocation."""

    def bind_candidates(
        self,
        *,
        invocation_id: str,
        candidates: Sequence[KnowledgeCandidate | Mapping[str, Any]],
        policy: CitationValidationPolicy | None = None,
        corpus_status: KnowledgeCorpusStatus = KnowledgeCorpusStatus.AVAILABLE,
        query_summary: str | None = None,
    ) -> KnowledgeRetrieval:
        if not invocation_id.strip():
            raise ValueError("Knowledge candidate binding requires an invocation ID")
        active_policy = policy or CitationValidationPolicy()
        if corpus_status in {
            KnowledgeCorpusStatus.EMPTY,
            KnowledgeCorpusStatus.UNAVAILABLE,
            KnowledgeCorpusStatus.ERROR,
        }:
            if candidates:
                raise ValueError(f"{corpus_status.value} retrieval cannot bind candidates")
            return KnowledgeRetrieval(
                corpus_status=corpus_status,
                required_applicability=active_policy.required_applicability,
                query_summary=query_summary,
            )

        all_candidates: list[KnowledgeCandidate] = []
        eligible: list[KnowledgeCandidate] = []
        rejected: list[RejectedKnowledgeCandidate] = []
        required = set(active_policy.required_applicability)
        for raw in candidates:
            candidate = (
                raw
                if isinstance(raw, KnowledgeCandidate)
                else KnowledgeCandidate.model_validate(raw)
            )
            all_candidates.append(candidate)
            reason: CandidateRejectionReason | None = None
            if not candidate.active:
                reason = CandidateRejectionReason.INACTIVE
            elif candidate.review_status is not KnowledgeReviewStatus.APPROVED:
                reason = CandidateRejectionReason.NOT_APPROVED
            elif not required.issubset(candidate.applicability):
                reason = CandidateRejectionReason.INAPPLICABLE
            elif not candidate.selected_for_adoption:
                reason = CandidateRejectionReason.NOT_SELECTED
            if reason is None:
                eligible.append(candidate)
            else:
                rejected.append(
                    RejectedKnowledgeCandidate(
                        candidate_id=candidate.candidate_id,
                        reason=reason,
                    )
                )
        candidate_ids = [candidate.candidate_id for candidate in all_candidates]
        citation_ids = [candidate.citation_id for candidate in all_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Repository returned duplicate knowledge candidate IDs")
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("Repository returned duplicate knowledge citation IDs")
        return KnowledgeRetrieval(
            corpus_status=KnowledgeCorpusStatus.AVAILABLE,
            required_applicability=active_policy.required_applicability,
            candidates=tuple(all_candidates),
            citations=tuple(candidate.bind(invocation_id) for candidate in eligible),
            rejected_candidates=tuple(rejected),
            query_summary=query_summary,
        )

    def bind_search_result(
        self,
        *,
        invocation_id: str,
        result: Mapping[str, Any],
        policy: CitationValidationPolicy | None = None,
    ) -> KnowledgeRetrieval:
        """Normalize an adapter search mapping and overwrite invocation provenance."""

        status = KnowledgeCorpusStatus(result.get("corpus_status", result.get("status")))
        raw_candidates = result.get("candidates", result.get("citations", ()))
        if isinstance(raw_candidates, (str, bytes)) or not isinstance(
            raw_candidates, Sequence
        ):
            raise ValueError("Knowledge search candidates must be a sequence")
        summary = result.get("query_summary")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("Knowledge query summary must be a string")
        return self.bind_candidates(
            invocation_id=invocation_id,
            candidates=raw_candidates,
            policy=policy,
            corpus_status=status,
            query_summary=summary,
        )


def bind_candidates(
    invocation_id: str,
    candidates: Sequence[KnowledgeCandidate | Mapping[str, Any]],
    *,
    policy: CitationValidationPolicy | None = None,
    query_summary: str | None = None,
) -> KnowledgeRetrieval:
    """Concise function API for coordinators that already have an invocation ID."""

    return KnowledgeCandidateBinder().bind_candidates(
        invocation_id=invocation_id,
        candidates=candidates,
        policy=policy,
        query_summary=query_summary,
    )


class NutritionCitationValidator:
    """Require complete, eligible, unchanged citations for every RAG claim."""

    def validate(
        self,
        *,
        invocation_id: str,
        context: NutritionContext,
        assessment: ProfessionalAssessment,
    ) -> CitationValidationReport:
        issues: list[NutritionValidationIssue] = []
        retrieval = context.knowledge
        candidates = {candidate.citation_id: candidate for candidate in retrieval.candidates}
        allowed = {citation.citation_id: citation for citation in retrieval.citations}
        adopted = {citation.citation_id: citation for citation in assessment.citations}

        if retrieval.corpus_status in {
            KnowledgeCorpusStatus.EMPTY,
            KnowledgeCorpusStatus.UNAVAILABLE,
            KnowledgeCorpusStatus.ERROR,
        } and assessment.citations:
            issues.append(
                NutritionValidationIssue(
                    NutritionValidationIssueCode.EMPTY_CORPUS_CITED,
                    "citations",
                )
            )

        for citation_id, citation in adopted.items():
            expected = allowed.get(citation_id)
            candidate = candidates.get(citation_id)
            if expected is None or candidate is None:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.UNKNOWN_CITATION,
                        citation_id,
                    )
                )
                continue
            if expected.model_dump(mode="json") != citation.model_dump(mode="json"):
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CITATION_CHANGED,
                        citation_id,
                    )
                )
            if citation.retrieved_in_invocation_id != invocation_id:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CITATION_INVOCATION_MISMATCH,
                        citation_id,
                    )
                )
            if citation.review_status is not KnowledgeReviewStatus.APPROVED:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CITATION_NOT_APPROVED,
                        citation_id,
                    )
                )
            if not candidate.active:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CITATION_INACTIVE,
                        citation_id,
                    )
                )
            if not set(retrieval.required_applicability).issubset(
                citation.applicability
            ):
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.CITATION_INAPPLICABLE,
                        citation_id,
                    )
                )

        rag_claims = [
            claim for claim in assessment.findings if ClaimBasis.RAG_EVIDENCE in claim.basis_types
        ]
        covered = 0
        used_citation_ids: set[str] = set()
        for claim in assessment.findings:
            refs = set(claim.knowledge_refs)
            resolved_ids = {
                citation.citation_id
                for citation in assessment.citations
                if citation.citation_id in refs
                or citation.chunk_id in refs
                or citation.source_id in refs
            }
            if ClaimBasis.RAG_EVIDENCE in claim.basis_types:
                valid_ids = resolved_ids.intersection(allowed).intersection(candidates)
                if valid_ids:
                    covered += 1
                    used_citation_ids.update(valid_ids)
                else:
                    issues.append(
                        NutritionValidationIssue(
                            NutritionValidationIssueCode.RAG_CLAIM_UNCOVERED,
                            claim.claim_id,
                        )
                    )
            elif refs:
                issues.append(
                    NutritionValidationIssue(
                        NutritionValidationIssueCode.NON_RAG_CITATION,
                        claim.claim_id,
                    )
                )

        for citation_id in sorted(set(adopted).difference(used_citation_ids)):
            issues.append(
                NutritionValidationIssue(
                    NutritionValidationIssueCode.UNUSED_CITATION,
                    citation_id,
                )
            )

        return CitationValidationReport(
            issues=tuple(issues),
            rag_claim_count=len(rag_claims),
            covered_rag_claim_count=covered,
        )


__all__ = [
    "CitationValidationPolicy",
    "CitationValidationReport",
    "KnowledgeCandidateBinder",
    "NutritionCitationValidator",
    "NutritionKnowledgeRepositoryAdapter",
    "bind_candidates",
]
