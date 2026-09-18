from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StyleInput(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class ExampleInput(Input):
    user_input: str = Field(min_length=1, max_length=4000)
    original_response: str = Field(min_length=1, max_length=4000)
    desired_response: str = Field(min_length=1, max_length=4000)


class ExampleState(Input):
    status: Literal["pending", "approved", "excluded", "conflict"]
    reason: str = Field(default="", max_length=2000)


class ReviewInput(Input):
    style_match: int = Field(ge=1, le=5, strict=True)
    fidelity: int = Field(ge=1, le=5, strict=True)
    appropriateness: int = Field(ge=1, le=5, strict=True)
    decision: Literal["accept", "reject"]
    reason: str = Field(default="", max_length=2000)
    desired_response: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_reason(self) -> ReviewInput:
        if self.decision == "reject" and not self.reason:
            raise ValueError("拒绝时必须填写理由")
        return self


class GuideRule(Input):
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Literal["stable", "candidate", "conflict"]


class Guide(Input):
    summary: str = Field(min_length=1, max_length=1000)
    rules: list[GuideRule] = Field(min_length=1, max_length=32)
    prohibited_phrases: list[str] = Field(default_factory=list, max_length=64)


class Analysis(Input):
    example_id: str
    category: Literal["expression", "content_change", "conflict", "unusable"]
    reason: str = Field(min_length=1, max_length=1000)
    expression_rule: str = Field(default="", max_length=500)


class AnalysisBatch(Input):
    items: list[Analysis]
