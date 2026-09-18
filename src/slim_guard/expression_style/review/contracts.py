"""Explicit review decisions; reviewers never produce replacement replies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.agent_models.gateway import ModelResponse
from slim_guard.expression_style.review.integrity import StyleValidationReport

if TYPE_CHECKING:
    from slim_guard.agents.contracts import AgentInvocation, StyledResponse
    from slim_guard.expression_style.contracts import StyleContext
    from slim_guard.runtime.invocation import InvocationGrant


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal["fidelity", "expression"]
    severity: Literal["repairable", "blocking", "warning"]
    code: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1000)
    source_excerpt: str = Field(max_length=2000)
    output_excerpt: str = Field(max_length=2000)
    repair_requirement: str = Field(min_length=1, max_length=1000)


class ReplyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fidelity_passed: bool = Field(strict=True)
    expression_passed: bool = Field(strict=True)
    issues: list[ReviewIssue] = Field(max_length=12)

    @model_validator(mode="after")
    def consistent(self) -> ReplyCheck:
        for dimension, passed in (
            ("fidelity", self.fidelity_passed),
            ("expression", self.expression_passed),
        ):
            failures = [
                i for i in self.issues if i.dimension == dimension and i.severity != "warning"
            ]
            if passed == bool(failures):
                raise ValueError("审查判决与问题列表不一致")
        return self


@dataclass(frozen=True, slots=True)
class RewriteReviewResult:
    passed: bool
    issues: tuple[str, ...]
    failure_code: str | None = None
    responses: tuple[ModelResponse, ...] = ()
    total_token_count: int = 0
    integrity: StyleValidationReport = StyleValidationReport()
    policy_version: str = "style-review-v2"
    verdict: str = "inconclusive"
    details: tuple[dict[str, object], ...] = ()
    signature: str = ""
    checks: tuple[dict[str, str], ...] = ()


class StyleReviewPort(Protocol):
    async def review(
        self,
        *,
        invocation: AgentInvocation,
        context: StyleContext,
        response: StyledResponse,
        source_text: str,
        remaining_tokens: int,
        grant: InvocationGrant | None = None,
    ) -> RewriteReviewResult: ...
