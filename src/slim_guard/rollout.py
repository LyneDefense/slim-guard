"""Read-only release gates over operator-reviewed regression and runtime evidence.

This validates a report, not the truth of its input and not production permission.
No model is allowed to approve a deployment or manufacture missing test evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from slim_guard.agents.contracts import ContractModel

REQUIRED_REGRESSIONS = frozenset(
    {
        "current_and_target_weight",
        "current_and_target_body_fat",
        "idempotent_writes",
        "save_previous_and_resume",
        "height_correction",
        "clear_blurry_multiple_images",
        "image_reference",
        "chitchat_and_refusal",
        "weight_plateau",
        "reported_health_context",
        "empty_conflicting_retired_forged_rag",
        "style_fact_risk_uncertainty_drift",
        "reviewer_return_edges",
        "emergency_style_bypass",
        "provider_timeout_legacy_fallback",
        "turn_budget_exhaustion",
        "rollback_preserves_thread_and_records",
    }
)


class RegressionEvidence(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    passed: bool = Field(strict=True)
    evidence_ref: str = Field(min_length=1, max_length=1000)

    @field_validator("case_id", "evidence_ref")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Regression evidence cannot be blank")
        return value.strip()


class RolloutEvidence(ContractModel):
    target_stage: Literal["test_canary", "small_canary", "on"]
    actor: str = Field(min_length=1, max_length=128)
    graph_version: str = Field(min_length=1, max_length=128)
    agent_version: str = Field(min_length=1, max_length=128)
    profile_version: str = Field(min_length=1, max_length=128)
    evaluated_at: datetime
    metrics_ref: str = Field(min_length=1, max_length=1000)
    sampled_workflows: int = Field(ge=0, strict=True)
    rejected_workflows: int = Field(ge=0, strict=True)
    repaired_workflows: int = Field(ge=0, strict=True)
    degraded_workflows: int = Field(ge=0, strict=True)
    latency_p95_ms: float = Field(ge=0, allow_inf_nan=False)
    tokens_p95: float = Field(ge=0, allow_inf_nan=False)
    maximum_node_failure_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    rag_enabled: bool = Field(strict=True)
    rag_claims: int = Field(ge=0, strict=True)
    covered_rag_claims: int = Field(ge=0, strict=True)
    invalid_citations: int = Field(ge=0, strict=True)
    critical_safety_failures: int = Field(ge=0, strict=True)
    duplicate_business_writes: int = Field(ge=0, strict=True)
    quality_pair_count: int = Field(ge=0, strict=True)
    legacy_quality_mean: float = Field(ge=1, le=5, allow_inf_nan=False)
    candidate_quality_mean: float = Field(ge=1, le=5, allow_inf_nan=False)
    quality_review_ref: str = Field(min_length=1, max_length=1000)
    real_failure_sample_count: int = Field(ge=0, strict=True)
    real_failure_retests_passed: int = Field(ge=0, strict=True)
    regressions: tuple[RegressionEvidence, ...]

    @field_validator(
        "actor",
        "graph_version",
        "agent_version",
        "profile_version",
        "metrics_ref",
        "quality_review_ref",
    )
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Review identity and evidence references cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.evaluated_at.utcoffset() is None:
            raise ValueError("Evaluation timestamp must be timezone-aware")
        if (
            max(
                self.rejected_workflows,
                self.repaired_workflows,
                self.degraded_workflows,
                self.quality_pair_count,
            )
            > self.sampled_workflows
        ):
            raise ValueError("Outcome counts exceed sampled workflows")
        if self.covered_rag_claims > self.rag_claims:
            raise ValueError("Covered RAG claims exceed total claims")
        if self.real_failure_retests_passed > self.real_failure_sample_count:
            raise ValueError("Retest passes exceed real failure samples")
        if len({case.case_id for case in self.regressions}) != len(self.regressions):
            raise ValueError("Regression case IDs must be unique")
        return self


class RolloutDecision(ContractModel):
    passed: bool
    target_stage: str
    reasons: tuple[str, ...]
    minimum_samples: int
    minimum_quality_pairs: int
    deployment_performed: Literal[False] = False


def check_rollout(evidence: RolloutEvidence, *, now: datetime | None = None) -> RolloutDecision:
    """Conservative initial thresholds; revisions must be reviewed in code."""
    now = now or datetime.now(UTC)
    if now.utcoffset() is None:
        raise ValueError("Gate clock must be timezone-aware")
    samples, quality_pairs = {
        "test_canary": (20, 20),
        "small_canary": (100, 50),
        "on": (500, 100),
    }[evidence.target_stage]
    reasons: list[str] = []
    if not now - timedelta(days=7) <= evidence.evaluated_at <= now:
        reasons.append("evidence_stale_or_future")
    if evidence.sampled_workflows < samples:
        reasons.append("insufficient_runtime_samples")
    if evidence.quality_pair_count < quality_pairs:
        reasons.append("insufficient_human_quality_pairs")
    if evidence.candidate_quality_mean < evidence.legacy_quality_mean:
        reasons.append("candidate_quality_regression")
    for name, count, ceiling in (
        ("rejection", evidence.rejected_workflows, 0.02),
        ("repair", evidence.repaired_workflows, 0.15),
        ("degradation", evidence.degraded_workflows, 0.05),
    ):
        if evidence.sampled_workflows and count / evidence.sampled_workflows > ceiling:
            reasons.append(f"{name}_rate_exceeded")
    if evidence.latency_p95_ms > 60_000:
        reasons.append("latency_p95_exceeded")
    if evidence.tokens_p95 > 64_000:
        reasons.append("tokens_p95_exceeded")
    if evidence.maximum_node_failure_rate > 0.02:
        reasons.append("node_failure_rate_exceeded")
    if evidence.rag_enabled and evidence.rag_claims == 0:
        reasons.append("rag_claim_samples_missing")
    if evidence.covered_rag_claims != evidence.rag_claims:
        reasons.append("rag_coverage_incomplete")
    if evidence.invalid_citations:
        reasons.append("invalid_citations_present")
    if evidence.critical_safety_failures:
        reasons.append("critical_safety_failures_present")
    if evidence.duplicate_business_writes:
        reasons.append("duplicate_business_writes_present")
    if evidence.real_failure_sample_count != evidence.real_failure_retests_passed:
        reasons.append("real_failure_retests_incomplete")
    passed_cases = {case.case_id for case in evidence.regressions if case.passed}
    reasons.extend(
        f"regression_missing_or_failed:{case}"
        for case in sorted(REQUIRED_REGRESSIONS - passed_cases)
    )
    if any(not case.passed for case in evidence.regressions):
        reasons.append("regression_failure_present")
    return RolloutDecision(
        passed=not reasons,
        target_stage=evidence.target_stage,
        reasons=tuple(reasons),
        minimum_samples=samples,
        minimum_quality_pairs=quality_pairs,
    )
