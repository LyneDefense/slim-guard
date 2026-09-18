"""Explicit review decisions; reviewers never produce replacement replies."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.agent_models.gateway import ModelResponse
from slim_guard.expression_style.review.integrity import StyleValidationReport


class SemanticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool = Field(strict=True)
    issues: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def consistent(self) -> SemanticCheck:
        if self.passed == bool(self.issues):
            raise ValueError("Semantic verdict and reasons disagree")
        return self


@dataclass(frozen=True, slots=True)
class RewriteReviewResult:
    passed: bool
    issues: tuple[str, ...]
    failure_code: str | None = None
    responses: tuple[ModelResponse, ...] = ()
    total_token_count: int = 0
    integrity: StyleValidationReport = StyleValidationReport()
    policy_version: str = "style-review-v1"
