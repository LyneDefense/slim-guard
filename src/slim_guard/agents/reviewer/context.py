"""Compile the least context needed for response review."""

from __future__ import annotations

from collections.abc import Sequence

from slim_guard.agents.contracts import (
    ProfessionalAssessment,
    ResponsePlan,
    StyledResponse,
    TurnDirective,
)
from slim_guard.agents.reviewer.contracts import ReviewerContext, ReviewerEvidenceSummary
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1, StyleProfile


class ReviewerContextCompiler:
    def compile(
        self,
        *,
        turn_id: str,
        response_plan: ResponsePlan,
        styled_response: StyledResponse,
        assessment: ProfessionalAssessment | None = None,
        style_profile: StyleProfile = SLIMGUARD_DEFAULT_V1,
        available_evidence_ids: Sequence[str] | None = None,
        directive: TurnDirective | None = None,
        evidence_summaries: Sequence[ReviewerEvidenceSummary] = (),
    ) -> ReviewerContext:
        return ReviewerContext(
            turn_id=turn_id,
            response_plan=response_plan,
            styled_response=styled_response,
            style_profile=style_profile,
            assessment=assessment,
            directive=directive,
            evidence_summaries=tuple(evidence_summaries),
            available_evidence_ids=(
                tuple(available_evidence_ids) if available_evidence_ids is not None else None
            ),
        )


__all__ = ["ReviewerContextCompiler"]
