"""Small structured outputs for the trainer's bounded steps."""

from typing import Literal

from pydantic import Field

from slim_guard.expression_style.package import Artifact, Guide

WORKFLOW_VERSION = "style-preview-v3-coach-scope"


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
        "out_of_scope",
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


class TestCase(Artifact):
    id: str = Field(min_length=1, max_length=100)
    family: str = Field(min_length=1, max_length=100)
    user_input: str = Field(min_length=1, max_length=4000)
    context: tuple[str, ...] = Field(default=(), max_length=8)
    source_text: str = Field(min_length=1, max_length=4000)
    protected_literals: tuple[str, ...] = ()
    origin: Literal["fixed", "material", "feedback"] = "fixed"


class PreviewSuite(Artifact):
    version: str
    cases: tuple[TestCase, ...] = Field(min_length=1)


class BuildExecutionConfig(Artifact):
    max_seconds: int = Field(default=3600, ge=120, le=7200)
    request_seconds: int = Field(default=120, ge=10, le=300)
    max_request_bytes: int = Field(default=100_000, ge=4096, le=200_000)
