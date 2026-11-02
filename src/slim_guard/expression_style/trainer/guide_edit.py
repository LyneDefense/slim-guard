"""Human-authored guide revisions produce a new frozen package, never mutate a release."""

from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, StringConstraints

from slim_guard.expression_style.package import (
    Artifact,
    Evidence,
    Guide,
    GuideRule,
    StylePackage,
    make_package,
)

from .contracts import Material, MaterialAnalysis


class EditableRule(Artifact):
    rule_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)
    boundary: str = Field(min_length=1, max_length=500)


class EditableGuide(Artifact):
    summary: str = Field(min_length=1, max_length=1000)
    rules: tuple[EditableRule, ...] = Field(default=(), max_length=32)
    prohibited_phrases: tuple[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)], ...
    ] = Field(default=(), max_length=32)


def revise_guide(
    source: StylePackage,
    guide: EditableGuide,
    version_id: str,
) -> tuple[StylePackage, tuple[Material, ...], tuple[MaterialAnalysis, ...]]:
    opinions = (
        guide.summary,
        *(f"{r.text}\n适用边界：{r.boundary}" for r in guide.rules),
        *(f"避免表达：{phrase}" for phrase in guide.prohibited_phrases),
    )
    materials = tuple(
        Material(
            id=str(uuid5(NAMESPACE_URL, f"style-guide:{version_id}:{index}")),
            revision=1,
            user_input="人工编辑表达指引",
            original_response=source.guide.summary,
            correction_opinion=opinion,
        )
        for index, opinion in enumerate(opinions)
    )
    rules = tuple(
        GuideRule(
            rule_id=r.rule_id,
            text=r.text,
            boundary=r.boundary,
            evidence=(Evidence(example_id=materials[index + 1].id, revision=1, quote=r.text),),
        )
        for index, r in enumerate(guide.rules)
    )
    remaining = {r.rule_id for r in rules}
    examples = tuple(
        e.model_copy(update={"rule_ids": tuple(r for r in e.rule_ids if r in remaining)})
        for e in source.examples
        if remaining.intersection(e.rule_ids)
    )
    package = make_package(
        source.style_id,
        version_id,
        source.display_name,
        Guide(summary=guide.summary, rules=rules, prohibited_phrases=guide.prohibited_phrases),
        examples,
    )
    analyses = tuple(
        MaterialAnalysis(
            example_id=m.id,
            role="negative",
            reason="人工直接编辑的表达要求；不作为正面表达对",
        )
        for m in materials
    )
    return package, materials, analyses
