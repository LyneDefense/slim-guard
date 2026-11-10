"""Editable personas and immutable releases independent of the retired trainer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import StyleExample, StyleProfile, StyleProfileSnapshot
from .dialogue import DialogueMessage, validate_dialogue
from .package import MAX_PROMPT_BYTES, StylePackage, canonical, content_hash


class PersonaValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DialogueExample(PersonaValue):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=100)
    messages: tuple[DialogueMessage, ...] = Field(min_length=2, max_length=24)

    @model_validator(mode="after")
    def complete_dialogue(self) -> DialogueExample:
        validate_dialogue(self.messages)
        if any(len(message.content) > 4000 for message in self.messages):
            raise ValueError("对话示例的每条消息最多 4000 字")
        return self


class PersonaContent(PersonaValue):
    expression: str = Field(default="", max_length=1000)
    avoid: str = Field(default="", max_length=1000)
    examples: tuple[DialogueExample, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_examples(self) -> PersonaContent:
        if len({example.id for example in self.examples}) != len(self.examples):
            raise ValueError("对话示例 ID 重复")
        return self

    @property
    def content_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def compile_persona(content: PersonaContent) -> str:
    if not content.expression:
        raise ValueError("请先填写角色表达设定")
    compiled = canonical(
        {
            "summary": content.expression,
            "rules": [{"text": content.expression}]
            + ([{"text": f"避免的表达：{content.avoid}"}] if content.avoid else []),
            "prohibited_phrases": [],
            "dialogue_examples": [
                {"id": item.id, "messages": [m.model_dump(mode="json") for m in item.messages]}
                for item in content.examples
            ],
        }
    )
    if len(compiled.encode()) > MAX_PROMPT_BYTES:
        raise ValueError("角色设定和示例超过提示预算，请精简后再检查或试聊")
    return compiled


class PersonaPackage(PersonaValue):
    schema_version: Literal["persona-1"] = "persona-1"
    compiler_version: Literal["persona-v1"] = "persona-v1"
    style_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    content: PersonaContent
    compiled_prompt: str
    package_hash: str

    @model_validator(mode="after")
    def verify(self) -> PersonaPackage:
        if self.compiled_prompt != compile_persona(self.content):
            raise ValueError("角色编译结果与冻结产物不一致")
        if self.package_hash != content_hash(
            self.model_dump(mode="json", exclude={"package_hash"})
        ):
            raise ValueError("角色产物 Hash 不一致")
        return self

    def runtime_snapshot(self) -> StyleProfileSnapshot:
        return StyleProfileSnapshot(
            profile=StyleProfile(
                profile_id=self.style_id,
                version=self.version_id,
                display_name=self.display_name,
                description=self.content.expression,
                tone_rules=(self.content.expression,)
                + ((f"避免的表达：{self.content.avoid}",) if self.content.avoid else ()),
                prohibited_phrases=(),
            ),
            examples=tuple(
                StyleExample(
                    example_id=item.id,
                    style_profile_version=self.version_id,
                    text=item.messages[-1].content,
                    user_input=item.messages[-2].content,
                    dialogue=item.messages,
                )
                for item in self.content.examples
            ),
            compiled_prompt=self.compiled_prompt,
            package_hash=self.package_hash,
        )


def make_persona_package(
    style_id: str, version_id: str, name: str, content: PersonaContent
) -> PersonaPackage:
    values = {
        "schema_version": "persona-1",
        "compiler_version": "persona-v1",
        "style_id": style_id,
        "version_id": version_id,
        "display_name": name,
        "content": content.model_dump(mode="json"),
        "compiled_prompt": compile_persona(content),
    }
    return PersonaPackage.model_validate({**values, "package_hash": content_hash(values)})


def read_package(value: object) -> PersonaPackage | StylePackage:
    if isinstance(value, dict) and value.get("schema_version") == "persona-1":
        return PersonaPackage.model_validate(value)
    return StylePackage.model_validate(value)


def editable_content(value: object) -> PersonaContent:
    """Convert a legacy release without silently discarding its expression rules."""
    package = read_package(value)
    if isinstance(package, PersonaPackage):
        return package.content
    if any(not item.dialogue and not item.user_input for item in package.examples):
        raise ValueError("旧版本含缺少用户上下文的回答示例，请查看历史配置后手动整理为完整对话")
    expression = "\n".join(
        [package.guide.summary]
        + [f"{rule.text}（边界：{rule.boundary}）" for rule in package.guide.rules]
    )
    return PersonaContent(
        expression=expression,
        avoid="\n".join(package.guide.prohibited_phrases),
        examples=tuple(
            DialogueExample(
                id=item.id,
                messages=item.dialogue
                or (
                    DialogueMessage(role="user", content=item.user_input),
                    DialogueMessage(role="assistant", content=item.desired_response),
                ),
            )
            for item in package.examples
            if item.dialogue or item.user_input
        ),
    )
