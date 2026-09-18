"""Joint guide/example construction, grounded in material revisions."""

from typing import Any

from slim_guard.expression_style.package import FixedExample, Guide, StylePackage, make_package

from .client import TrainingClient
from .contracts import CandidateProposal, ConsistencyCheck, Material, MaterialAnalysis


def validate_proposal(
    proposal: CandidateProposal,
    materials: tuple[Material, ...],
    analyses: tuple[MaterialAnalysis, ...],
    style_id: str,
    version_id: str,
    name: str,
) -> StylePackage:
    indexed = {m.id: m for m in materials}
    roles = {a.example_id: a.role for a in analyses}
    if len(set(proposal.example_ids)) != len(proposal.example_ids):
        raise ValueError("选入示例重复")
    for rule in proposal.guide.rules:
        for evidence in rule.evidence:
            material = indexed.get(evidence.example_id)
            if material is None or material.revision != evidence.revision:
                raise ValueError("规则证据修订不存在")
            if roles[material.id] not in {"positive", "negative"}:
                raise ValueError("不可用素材不能作为风格规则的支持证据")
            if not any(
                evidence.quote in text
                for text in (
                    material.original_response,
                    material.desired_response,
                    material.correction_opinion,
                )
            ):
                raise ValueError("规则证据片段不在原始素材中")
        if set(rule.counterexample_ids) - indexed.keys():
            raise ValueError("反例不存在")
    examples = []
    for identifier in proposal.example_ids:
        if identifier not in indexed or roles[identifier] != "positive":
            raise ValueError("固定示例必须来自合格正面表达对")
        material = indexed[identifier]
        rules = tuple(
            r.rule_id
            for r in proposal.guide.rules
            if identifier in {e.example_id for e in r.evidence}
        )
        if not rules:
            raise ValueError("固定示例必须与 Guide 的证据关联")
        examples.append(
            FixedExample(
                id=identifier,
                revision=material.revision,
                original_response=material.original_response,
                desired_response=material.desired_response,
                rule_ids=rules,
            )
        )
    return make_package(style_id, version_id, name, proposal.guide, tuple(examples))


async def construct(
    client: TrainingClient,
    materials: tuple[Material, ...],
    analyses: tuple[MaterialAnalysis, ...],
    *,
    style_id: str,
    version_id: str,
    name: str,
    feedback: list[dict[str, Any]],
    previous: StylePackage | None = None,
    failures: list[dict[str, Any]] | None = None,
) -> tuple[StylePackage, str]:
    # Map/reduce consumes all material without sending a 300-record prompt.
    guide_parts = []
    analysis_by_id = {a.example_id: a.model_dump(mode="json") for a in analyses}
    for start in range(0, len(materials), 16):
        batch = materials[start : start + 16]
        part = await client.ask(
            Guide,
            "从本批素材提炼有边界的表达规律，证据必须带素材ID、revision和逐字片段。"
            "不要按数量投票，负面意见可以限制规则但不得伪造期望回答。"
            "只从 positive/negative 素材产生规则，其他素材保留为反例，不强行统一冲突。"
            "一条明确意见可产生局部规则，不冒充全局规律。相同规则用同一 rule_id。",
            [{**m.model_dump(mode="json"), "analysis": analysis_by_id[m.id]} for m in batch],
            stage="construction",
        )
        guide_parts.append(part.model_dump(mode="json"))
    while len(guide_parts) > 1:
        merged = []
        for start in range(0, len(guide_parts), 6):
            guide = await client.ask(
                Guide,
                "合并有证据的表达规律，保留原始证据片段/修订，不制造规则或证据。"
                "检查相互矛盾并收窄边界，不能只保留支持多数观点的证据。",
                guide_parts[start : start + 6],
                stage="construction",
            )
            merged.append(guide.model_dump(mode="json"))
        guide_parts = merged
    evidence_ids = {e["example_id"] for r in guide_parts[0]["rules"] for e in r["evidence"]}
    eligible = {a.example_id for a in analyses if a.role == "positive"} & evidence_ids
    payload = {
        "guide": guide_parts[0],
        "eligible_examples": [m.model_dump(mode="json") for m in materials if m.id in eligible],
        "feedback": feedback,
        "previous": previous.model_dump(mode="json") if previous else None,
        "development_failures": failures or [],
    }
    repair_issues: list[str] = []
    for attempt in range(1, 3):
        proposal = await client.ask(
            CandidateProposal,
            "联合确定 Guide 和固定表达示例。不固定示例数量，选最小互补集合，所有示例ID"
            "都必须是 Guide 的支持证据。不要加入意图分类、用户事实或专业意见。"
            "纠正意见优先用于解决具体问题；不能为提高评测分数修改测试题或审查策略。"
            "若存在无法隔离的冲突，不伪造一致。explanation 说明实际调整及理由。",
            {**payload, "repair_issues": repair_issues},
            stage="optimization" if previous else "construction",
        )
        try:
            package = validate_proposal(proposal, materials, analyses, style_id, version_id, name)
        except ValueError as exc:
            repair_issues = [str(exc)]
        else:
            await client.port.save(
                "draft",
                package.model_dump(mode="json"),
                stage="construction",
                message=f"联合风格包第 {attempt} 次生成，等待独立一致性审查",
            )
            repair_issues = await audit_package(client, package, materials, analysis_by_id)
            if not repair_issues:
                return package, proposal.explanation
        await client.port.event(
            "consistency",
            "联合构建未通过：" + "；".join(repair_issues),
            attempt=attempt,
        )
        payload["rejected_proposal"] = proposal.model_dump(mode="json")
    raise ValueError("联合构建两次审查仍未通过，请修订素材后新建任务：" + "；".join(repair_issues))


async def audit_package(
    client: TrainingClient,
    package: StylePackage,
    materials: tuple[Material, ...],
    analysis_by_id: dict[str, Any],
) -> list[str]:
    # Audit ALL material batches, including excluded/unselected examples, against the bundle.
    consistency_results = []
    issues: list[str] = []
    for start in range(0, len(materials), 16):
        check = await client.ask(
            ConsistencyCheck,
            "审查 Guide 和固定示例是否有证据、是否互相冲突、是否过度泛化。"
            "检查本批的反例及纠正意见是否被掩盖；已明确隔离的坏素材不必阻止构建。"
            "passed=true 必须 issues=[]；不可解决且影响使用的问题要求失败。",
            {
                "package": package.model_dump(mode="json"),
                "materials": [m.model_dump(mode="json") for m in materials[start : start + 16]],
                "analyses": [analysis_by_id[m.id] for m in materials[start : start + 16]],
            },
            stage="consistency",
        )
        consistency_results.append({"batch": start // 16 + 1, **check.model_dump(mode="json")})
        await client.port.save(
            "consistency",
            consistency_results,
            stage="consistency",
            message=f"一致性审查：第 {start // 16 + 1} 批",
        )
        if not check.passed or check.issues:
            issues.extend(check.issues or [f"第 {start // 16 + 1} 批一致性审查未通过"])
    return issues
