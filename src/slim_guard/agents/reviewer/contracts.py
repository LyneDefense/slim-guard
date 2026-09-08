"""Typed context, validation report, and result for response review."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from slim_guard.agent_models.gateway import ModelResponse
from slim_guard.agents.contracts import (
    ContractModel,
    InvocationStatus,
    ProfessionalAssessment,
    ResponsePlan,
    ReviewerIssueType,
    ReviewerVerdict,
    StyledResponse,
    TurnDirective,
)
from slim_guard.agents.style.contracts import StyleProfile


class ReviewerEvidenceSummary(ContractModel):
    """Coordinator-selected fact content; no database rows or raw conversations."""

    evidence_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=4000)

    @field_validator("evidence_id", "summary")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reviewer evidence fields cannot be blank")
        return value


class ReviewerContext(ContractModel):
    """Minimal immutable inputs visible to the reviewer model."""

    schema_version: Literal["1"] = "1"
    turn_id: str = Field(min_length=1, max_length=128)
    response_plan: ResponsePlan
    styled_response: StyledResponse
    style_profile: StyleProfile
    assessment: ProfessionalAssessment | None = None
    directive: TurnDirective | None = None
    available_evidence_ids: tuple[str, ...] | None = Field(default=None, max_length=128)
    evidence_summaries: tuple[ReviewerEvidenceSummary, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def validate_summaries(self) -> ReviewerContext:
        ids = tuple(item.evidence_id for item in self.evidence_summaries)
        if len(ids) != len(set(ids)):
            raise ValueError("Reviewer evidence summaries must have unique IDs")
        if self.available_evidence_ids is not None and not set(ids).issubset(
            self.available_evidence_ids
        ):
            raise ValueError("Reviewer evidence summaries must reference available evidence")
        return self

    @field_validator("available_evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        value: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        if any(not item.strip() for item in value):
            raise ValueError("Reviewer evidence IDs cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Reviewer evidence IDs must be unique")
        return value


class ReviewerValidationIssueCode(StrEnum):
    PASS_REASON_PRESENT = "pass_reason_present"
    NON_PASS_ISSUE_MISSING = "non_pass_issue_missing"
    NON_PASS_REASON_MISSING = "non_pass_reason_missing"
    INVALID_REPAIR_DIRECTION = "invalid_repair_direction"
    PASS_CONTRADICTS_RESPONSE = "pass_contradicts_response"
    DETECTED_ISSUE_NOT_DECLARED = "detected_issue_not_declared"


@dataclass(frozen=True, slots=True)
class ReviewerValidationIssue:
    code: ReviewerValidationIssueCode
    subject: str


@dataclass(frozen=True, slots=True)
class ReviewerValidationReport:
    issues: tuple[ReviewerValidationIssue, ...] = ()
    detected_issue_types: tuple[ReviewerIssueType, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code.value for issue in self.issues)


@dataclass(frozen=True, slots=True)
class ReviewerAgentResult:
    """Reviewer result; failures always carry a conservative reject verdict."""

    status: InvocationStatus
    verdict: ReviewerVerdict
    model_responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    used_fallback: bool
    repair_attempted: bool
    failure_code: str | None = None
    validation_report: ReviewerValidationReport = ReviewerValidationReport()


EvidenceIdsInput = Sequence[str] | None


__all__ = [
    "EvidenceIdsInput",
    "ReviewerAgentResult",
    "ReviewerContext",
    "ReviewerEvidenceSummary",
    "ReviewerValidationIssue",
    "ReviewerValidationIssueCode",
    "ReviewerValidationReport",
]
