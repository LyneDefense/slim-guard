"""Frozen evidence catalog, exact source bindings, and input-bound construction checks."""

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from slim_guard.expression_style.package import (
    Evidence,
    FixedExample,
    Guide,
    GuideRule,
    StylePackage,
    compile_prompt,
    make_package,
)

from .construction_contracts import ExampleSelection, GuideDraft
from .contracts import Material, MaterialAnalysis
from .output_validation import OutputValidation

EVIDENCE_FIELDS = ("original_response", "desired_response", "correction_opinion")


@dataclass(frozen=True)
class BoundEvidence:
    ref: str
    material_ref: str
    example_id: str
    revision: int
    field: str
    start: int
    end: int
    quote: str

    def location(self) -> str:
        return f"{self.example_id}@{self.revision}:{self.field}[{self.start}:{self.end}]"


class EvidenceCatalog:
    def __init__(
        self, materials: tuple[Material, ...], analyses: tuple[MaterialAnalysis, ...]
    ) -> None:
        ids = [m.id for m in materials]
        analysis_ids = [a.example_id for a in analyses]
        if len(set(ids)) != len(ids) or sorted(ids) != sorted(analysis_ids):
            raise ValueError("素材与分析必须逐条唯一对应")
        self.analyses = {a.example_id: a for a in analyses}
        self.materials = {
            f"m{i}": material
            for i, material in enumerate(
                (m for m in materials if self.analyses[m.id].role in {"positive", "negative"}),
                1,
            )
        }
        self.evidence: dict[str, BoundEvidence] = {}
        for material_ref, material in self.materials.items():
            for field in EVIDENCE_FIELDS:
                source = getattr(material, field)
                # Bound literal chunks to Evidence's existing schema, preserving exact offsets.
                for offset in range(0, len(source), 1000):
                    raw = source[offset : offset + 1000]
                    quote = raw.strip()
                    if not quote:
                        continue
                    start = offset + len(raw) - len(raw.lstrip())
                    ref = f"e{len(self.evidence) + 1}"
                    self.evidence[ref] = BoundEvidence(
                        ref,
                        material_ref,
                        material.id,
                        material.revision,
                        field,
                        start,
                        start + len(quote),
                        quote,
                    )
        if not self.evidence:
            raise ValueError("没有可用的表达原文或纠正意见")

    def payload(self, material_refs: Iterable[str]) -> dict[str, Any]:
        selected = set(material_refs)
        return {
            "materials": [
                {
                    "id": ref,
                    "user_input": m.user_input,
                    "role": self.analyses[m.id].role,
                    "analysis": {
                        "reason": self.analyses[m.id].reason,
                        "expression_rule": self.analyses[m.id].expression_rule,
                    },
                }
                for ref, m in self.materials.items()
                if ref in selected
            ],
            "evidence": self.evidence_payload(
                ref for ref, e in self.evidence.items() if e.material_ref in selected
            ),
        }

    def evidence_payload(self, refs: Iterable[str]) -> list[dict[str, str]]:
        selected = set(refs)
        return [
            {"ref": ref, "material_ref": e.material_ref, "field": e.field, "text": e.quote}
            for ref, e in self.evidence.items()
            if ref in selected
        ]

    @staticmethod
    def used_refs(guides: Iterable[GuideDraft]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                ref for guide in guides for rule in guide.rules for ref in rule.evidence_refs
            )
        )

    def bind(self, draft: GuideDraft) -> Guide:
        return Guide(
            summary=draft.summary,
            rules=tuple(
                GuideRule(
                    rule_id=r.rule_id,
                    text=r.text,
                    boundary=r.boundary,
                    evidence=tuple(
                        Evidence(
                            example_id=self.evidence[ref].example_id,
                            revision=self.evidence[ref].revision,
                            quote=self.evidence[ref].quote,
                        )
                        for ref in r.evidence_refs
                    ),
                )
                for r in draft.rules
            ),
            prohibited_phrases=draft.prohibited_phrases,
        )

    def guide_validation(self, allowed_refs: Iterable[str]) -> OutputValidation[GuideDraft]:
        allowed = tuple(dict.fromkeys(allowed_refs))

        def check(draft: GuideDraft) -> list[dict[str, Any]]:
            errors: list[dict[str, Any]] = []
            rule_ids: set[str] = set()
            for i, rule in enumerate(draft.rules):
                if rule.rule_id in rule_ids:
                    errors.append(
                        {
                            "path": f"rules.{i}.rule_id",
                            "type": "duplicate_rule",
                            "message": "规则 ID 重复，请合并或区分边界",
                        }
                    )
                rule_ids.add(rule.rule_id)
                seen: set[str] = set()
                for j, ref in enumerate(rule.evidence_refs):
                    path = f"rules.{i}.evidence_refs.{j}"
                    if ref not in allowed:
                        errors.append(
                            {
                                "path": path,
                                "type": "unknown_evidence",
                                "message": "证据编号不在本次输入中；允许值：" + ", ".join(allowed),
                            }
                        )
                    elif ref in seen:
                        errors.append(
                            {
                                "path": path,
                                "type": "duplicate_evidence",
                                "message": "同一规则不要重复引用证据",
                            }
                        )
                    seen.add(ref)
            if not errors:
                try:
                    compile_prompt(self.bind(draft), ())
                except ValueError as exc:
                    errors.append({"path": "rules", "type": "prompt_budget", "message": str(exc)})
            return errors

        return OutputValidation(
            version="bound-guide-v1",
            references={ref: self.evidence[ref].location() for ref in allowed},
            check=check,
        )

    def eligible_examples(self, guide: GuideDraft) -> dict[str, Material]:
        used_materials = {self.evidence[ref].material_ref for ref in self.used_refs((guide,))}
        return {
            ref: m
            for ref, m in self.materials.items()
            if ref in used_materials
            and self.analyses[m.id].role == "positive"
            and m.original_response
            and m.desired_response
        }

    def package(
        self,
        guide: GuideDraft,
        selection: ExampleSelection,
        style_id: str,
        version_id: str,
        name: str,
    ) -> StylePackage:
        eligible = self.eligible_examples(guide)
        if len(set(selection.example_ids)) != len(selection.example_ids):
            raise ValueError("固定示例重复")
        if set(selection.example_ids) - eligible.keys():
            raise ValueError("固定示例必须来自指引关联的合格正面表达对")
        examples = tuple(
            FixedExample(
                id=eligible[ref].id,
                revision=eligible[ref].revision,
                original_response=eligible[ref].original_response,
                desired_response=eligible[ref].desired_response,
                rule_ids=tuple(
                    r.rule_id
                    for r in guide.rules
                    if any(self.evidence[e].material_ref == ref for e in r.evidence_refs)
                ),
            )
            for ref in selection.example_ids
        )
        return make_package(style_id, version_id, name, self.bind(guide), examples)

    def selection_validation(
        self,
        guide: GuideDraft,
        style_id: str,
        version_id: str,
        name: str,
    ) -> OutputValidation[ExampleSelection]:
        eligible = self.eligible_examples(guide)

        def check(selection: ExampleSelection) -> list[dict[str, Any]]:
            errors: list[dict[str, Any]] = []
            seen: set[str] = set()
            for i, ref in enumerate(selection.example_ids):
                if ref not in eligible:
                    errors.append(
                        {
                            "path": f"example_ids.{i}",
                            "type": "unsupported_example",
                            "message": "只能选择本次 eligible_examples 中的编号："
                            + ", ".join(eligible),
                        }
                    )
                elif ref in seen:
                    errors.append(
                        {
                            "path": f"example_ids.{i}",
                            "type": "duplicate_example",
                            "message": "示例编号重复",
                        }
                    )
                seen.add(ref)
            if not errors:
                try:
                    self.package(guide, selection, style_id, version_id, name)
                except ValueError as exc:
                    errors.append(
                        {"path": "example_ids", "type": "package_budget", "message": str(exc)}
                    )
            return errors

        return OutputValidation(
            version="bound-example-selection-v1",
            references={ref: m.id for ref, m in eligible.items()},
            check=check,
        )

    def artifact(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self.evidence.values()]
