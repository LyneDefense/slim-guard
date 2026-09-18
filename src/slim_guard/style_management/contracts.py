"""Admin inputs only. Training artifacts live in expression_style."""

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
    desired_response: str = Field(default="", max_length=4000)
    correction_opinion: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def needs_feedback(self) -> "ExampleInput":
        if not self.desired_response and not self.correction_opinion:
            raise ValueError("期望回答与纠正意见至少填写一项")
        return self


class ReviewInput(Input):
    style_match: int = Field(ge=1, le=5, strict=True)
    fidelity: int = Field(ge=1, le=5, strict=True)
    appropriateness: int = Field(ge=1, le=5, strict=True)
    decision: Literal["accept", "reject"]
    concern: Literal["output", "test_case", "automated_review"] = "output"
    reason: str = Field(default="", max_length=2000)
    desired_response: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_reason(self) -> "ReviewInput":
        if self.decision == "reject" and not self.reason:
            raise ValueError("拒绝时必须填写理由")
        if self.concern != "output" and self.decision != "reject":
            raise ValueError("测试题或自动审查存在争议时不能接受放行")
        return self
