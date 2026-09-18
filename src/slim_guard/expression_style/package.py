"""Frozen, evidence-backed style artifacts and deterministic prompt compilation."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import StyleExample, StyleProfile, StyleProfileSnapshot

COMPILER_VERSION: Literal["fixed-style-v1"] = "fixed-style-v1"
MAX_PROMPT_BYTES = 24_000


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Evidence(Artifact):
    example_id: str
    revision: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=1000)


class GuideRule(Artifact):
    rule_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    boundary: str = Field(min_length=1, max_length=500)
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    counterexample_ids: tuple[str, ...] = ()


class Guide(Artifact):
    summary: str = Field(min_length=1, max_length=1000)
    rules: tuple[GuideRule, ...] = Field(default=(), max_length=32)
    prohibited_phrases: tuple[str, ...] = Field(default=(), max_length=32)


class FixedExample(Artifact):
    id: str
    revision: int = Field(ge=1)
    original_response: str = Field(min_length=1, max_length=4000)
    desired_response: str = Field(min_length=1, max_length=4000)
    rule_ids: tuple[str, ...] = Field(min_length=1)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def compile_prompt(guide: Guide, examples: tuple[FixedExample, ...]) -> str:
    # Evidence and source records are not copied into the online model context.
    payload = {
        "summary": guide.summary,
        "rules": [{"text": r.text, "boundary": r.boundary} for r in guide.rules],
        "prohibited_phrases": guide.prohibited_phrases,
        "fixed_examples": [
            {"original": e.original_response, "rewritten": e.desired_response} for e in examples
        ],
    }
    prompt = canonical(payload)
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        raise ValueError("固定风格包超过提示预算，需减少重复规则或示例；不会在线截断")
    return prompt


class StylePackage(Artifact):
    schema_version: Literal["1"] = "1"
    compiler_version: Literal["fixed-style-v1"] = COMPILER_VERSION
    style_id: str
    version_id: str
    display_name: str
    guide: Guide
    examples: tuple[FixedExample, ...] = ()
    compiled_prompt: str
    package_hash: str

    @model_validator(mode="after")
    def verify(self) -> StylePackage:
        rule_ids = {r.rule_id for r in self.guide.rules}
        if len(rule_ids) != len(self.guide.rules):
            raise ValueError("风格规则 ID 重复")
        if len({e.id for e in self.examples}) != len(self.examples):
            raise ValueError("固定示例 ID 重复")
        if any(set(e.rule_ids) - rule_ids for e in self.examples):
            raise ValueError("示例引用了不存在的规则")
        if self.compiled_prompt != compile_prompt(self.guide, self.examples):
            raise ValueError("风格编译结果与冻结产物不一致")
        if self.package_hash != content_hash(
            self.model_dump(mode="json", exclude={"package_hash"})
        ):
            raise ValueError("风格产物 Hash 不一致")
        return self

    def runtime_snapshot(self) -> StyleProfileSnapshot:
        return StyleProfileSnapshot(
            profile=StyleProfile(
                profile_id=self.style_id,
                version=self.version_id,
                display_name=self.display_name,
                description=self.guide.summary,
                tone_rules=tuple(dict.fromkeys(r.text for r in self.guide.rules))
                or ("忠实原意，自然简洁，不新增内容。",),
                prohibited_phrases=self.guide.prohibited_phrases,
            ),
            examples=tuple(
                StyleExample(
                    example_id=e.id,
                    style_profile_version=self.version_id,
                    original_response=e.original_response,
                    text=e.desired_response,
                )
                for e in self.examples
            ),
            compiled_prompt=self.compiled_prompt,
            package_hash=self.package_hash,
        )


def make_package(
    style_id: str,
    version_id: str,
    name: str,
    guide: Guide,
    examples: tuple[FixedExample, ...] = (),
) -> StylePackage:
    values = {
        "schema_version": "1",
        "compiler_version": COMPILER_VERSION,
        "style_id": style_id,
        "version_id": version_id,
        "display_name": name,
        "guide": guide.model_dump(mode="json"),
        "examples": [e.model_dump(mode="json") for e in examples],
        "compiled_prompt": compile_prompt(guide, examples),
    }
    return StylePackage.model_validate({**values, "package_hash": content_hash(values)})
