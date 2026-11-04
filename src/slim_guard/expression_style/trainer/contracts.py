"""Small structured outputs for the trainer's bounded steps."""

from typing import Literal

from pydantic import Field, field_validator

from slim_guard.expression_style.dialogue import DialogueMessage, validate_dialogue
from slim_guard.expression_style.package import Artifact

WORKFLOW_VERSION = "style-preview-v5-direct-dialogue"


class Material(Artifact):
    id: str
    revision: int = Field(ge=1)
    user_input: str
    original_response: str
    desired_response: str = ""
    correction_opinion: str = ""
    history: tuple[DialogueMessage, ...] = Field(default=(), max_length=22)

    @field_validator("history")
    @classmethod
    def complete_history(cls, value: tuple[DialogueMessage, ...]) -> tuple[DialogueMessage, ...]:
        validate_dialogue(value)
        return value


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


class TestCase(Artifact):
    id: str = Field(min_length=1, max_length=100)
    family: str = Field(min_length=1, max_length=100)
    user_input: str = Field(min_length=1, max_length=4000)
    context: tuple[str, ...] = Field(default=(), max_length=8)
    source_text: str = Field(default="", max_length=4000)
    protected_literals: tuple[str, ...] = ()
    origin: Literal["fixed", "material", "feedback"] = "fixed"
    history: tuple[DialogueMessage, ...] = Field(default=(), max_length=22)
    follow_up_inputs: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("history")
    @classmethod
    def complete_history(cls, value: tuple[DialogueMessage, ...]) -> tuple[DialogueMessage, ...]:
        validate_dialogue(value)
        return value

    @field_validator("follow_up_inputs")
    @classmethod
    def nonblank_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() or len(text) > 4000 for text in value):
            raise ValueError("后续用户消息不能为空或超过 4000 字")
        return value


class PreviewSuite(Artifact):
    version: str
    cases: tuple[TestCase, ...] = Field(min_length=1)


class BuildExecutionConfig(Artifact):
    max_seconds: int = Field(default=3600, ge=120, le=7200)
    request_seconds: int = Field(default=120, ge=10, le=300)
    max_request_bytes: int = Field(default=100_000, ge=4096, le=200_000)
