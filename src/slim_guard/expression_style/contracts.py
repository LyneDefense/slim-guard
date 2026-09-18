"""Typed inputs and configuration for the response-style boundary."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from slim_guard.agents.contracts import (
    ContractModel,
    ProfessionalAssessment,
    ResponsePlan,
)


class StyleProfile(ContractModel):
    """Versioned, reviewed expression rules; never a source of user facts."""

    schema_version: Literal["1"] = "1"
    profile_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    tone_rules: tuple[str, ...] = Field(min_length=1, max_length=32)
    prohibited_phrases: tuple[str, ...] = Field(default=(), max_length=64)
    preferred_max_paragraphs: int = Field(default=3, ge=1, le=12, strict=True)

    @field_validator("tone_rules", "prohibited_phrases")
    @classmethod
    def validate_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Style profile rules cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Style profile rules must be unique")
        return normalized


class StyleExample(ContractModel):
    """A reviewed, de-identified example scoped to one profile version."""

    example_id: str = Field(min_length=1, max_length=128)
    style_profile_version: str = Field(min_length=1, max_length=128)
    original_response: str = ""
    text: str = Field(min_length=1, max_length=4000)


class StyleContext(ContractModel):
    """Minimal immutable context visible to the style model."""

    schema_version: Literal["1"] = "1"
    turn_id: str = Field(min_length=1, max_length=128)
    response_plan: ResponsePlan
    profile: StyleProfile
    assessment: ProfessionalAssessment | None = None
    examples: tuple[StyleExample, ...] = ()
    user_input: str = ""
    minimal_context: tuple[str, ...] = ()
    protected_literals: tuple[str, ...] = ()
    compiled_prompt: str = ""
    package_hash: str = ""

    @model_validator(mode="after")
    def validate_examples(self) -> StyleContext:
        source = "\n".join(block.text for block in self.response_plan.content_blocks)
        if any(not literal or literal not in source for literal in self.protected_literals):
            raise ValueError("受保护字面量必须存在于原稿中")
        example_ids = tuple(example.example_id for example in self.examples)
        if len(example_ids) != len(set(example_ids)):
            raise ValueError("Style example IDs must be unique")
        foreign_profiles = {
            example.style_profile_version
            for example in self.examples
            if example.style_profile_version != self.profile.version
        }
        if foreign_profiles:
            raise ValueError("Style examples must belong to the selected profile version")
        return self


class StyleProfileSnapshot(ContractModel):
    """One published version and its immutable reviewed example library for a Turn."""

    profile: StyleProfile
    examples: tuple[StyleExample, ...] = ()
    compiled_prompt: str = ""
    package_hash: str = ""

    @model_validator(mode="after")
    def validate_library(self) -> StyleProfileSnapshot:
        if any(item.style_profile_version != self.profile.version for item in self.examples):
            raise ValueError("Style examples must belong to the selected profile version")
        if len({item.example_id for item in self.examples}) != len(self.examples):
            raise ValueError("Style example IDs must be unique")
        return self


class StyleProfileRepository(Protocol):
    async def get_runtime_snapshot(self, version: str) -> StyleProfileSnapshot | None: ...


SLIMGUARD_DEFAULT_V1 = StyleProfile(
    profile_id="slimguard_default",
    version="slimguard_default_v1",
    display_name="SlimGuard 默认简洁语气",
    description="接近现有微信回复体验：自然、简洁、明确，不冒充真人或新增判断。",
    tone_rules=(
        "使用自然简洁的中文微信语气",
        "先准确表达既定内容，再给必要的下一步",
        "普通确认保持短句，不堆叠标题或口号",
        "直接但不羞辱、不恐吓、不冒充医生或其他真人",
    ),
    prohibited_phrases=(
        "作为章医生",
        "我是章医生",
        "保证瘦",
        "一定能瘦",
    ),
    preferred_max_paragraphs=3,
)


__all__ = [
    "SLIMGUARD_DEFAULT_V1",
    "StyleContext",
    "StyleExample",
    "StyleProfile",
    "StyleProfileRepository",
    "StyleProfileSnapshot",
]
