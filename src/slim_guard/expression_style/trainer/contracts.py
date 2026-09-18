"""Small structured outputs for the trainer's bounded steps."""

from typing import Literal

from pydantic import Field

from slim_guard.expression_style.package import Artifact, Guide


class Material(Artifact):
    id: str
    revision: int = Field(ge=1)
    user_input: str
    original_response: str
    desired_response: str = ""
    correction_opinion: str = ""


class MaterialAnalysis(Artifact):
    example_id: str
    role: Literal[
        "positive",
        "negative",
        "content_change",
        "semantic_drift",
        "duplicate",
        "conflict",
        "missing_context",
        "unusable",
    ]
    reason: str = Field(min_length=1, max_length=1000)
    expression_rule: str = Field(default="", max_length=500)
    duplicate_of: str | None = None


class AnalysisBatch(Artifact):
    items: tuple[MaterialAnalysis, ...]


class CandidateProposal(Artifact):
    guide: Guide
    example_ids: tuple[str, ...]
    explanation: str = Field(min_length=1, max_length=2000)


class ConsistencyCheck(Artifact):
    passed: bool = Field(strict=True)
    issues: tuple[str, ...]


class TestCase(Artifact):
    id: str = Field(min_length=1, max_length=100)
    family: str = Field(min_length=1, max_length=100)
    user_input: str = Field(min_length=1, max_length=2000)
    context: tuple[str, ...] = Field(default=(), max_length=8)
    source_text: str = Field(min_length=1, max_length=2000)
    protected_literals: tuple[str, ...] = ()


class TestSuite(Artifact):
    development: tuple[TestCase, ...] = Field(min_length=6, max_length=10)
    acceptance: tuple[TestCase, ...] = Field(min_length=10, max_length=15)


class ExpressionScores(Artifact):
    fidelity: int = Field(ge=1, le=5)
    style_match: int = Field(ge=1, le=5)
    naturalness: int = Field(ge=1, le=5)
    appropriateness: int = Field(ge=1, le=5)


class Comparison(Artifact):
    winner: Literal["left", "right", "tie", "uncertain"]
    reason: str = Field(min_length=1, max_length=1000)
    left_scores: ExpressionScores
    right_scores: ExpressionScores


class BuildBudget(Artifact):
    max_rounds: int = Field(default=2, ge=1, le=4)
    max_calls: int = Field(default=350, ge=20, le=2000)
    max_tokens: int = Field(default=1_000_000, ge=10000, le=10_000_000)
    max_seconds: int = Field(default=3600, ge=120, le=7200)
    request_seconds: int = Field(default=120, ge=10, le=300)
    max_request_bytes: int = Field(default=100_000, ge=4096, le=200_000)
