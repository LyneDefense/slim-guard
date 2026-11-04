"""Extract validated guides, then select examples without reopening their provenance."""

from typing import Any

from slim_guard.expression_style.package import StylePackage
from slim_guard.group_chat import COACH_ROLE_POLICY

from .client import TrainingClient
from .construction_contracts import ExampleSelection, GuideDraft
from .construction_evidence import EvidenceCatalog
from .contracts import Material, MaterialAnalysis


async def construct(
    client: TrainingClient,
    materials: tuple[Material, ...],
    analyses: tuple[MaterialAnalysis, ...],
    *,
    style_id: str,
    version_id: str,
    name: str,
) -> tuple[StylePackage, str]:
    catalog = EvidenceCatalog(materials, analyses)
    await client.port.save(
        "evidence_catalog",
        catalog.artifact(),
        stage="construction",
        message="程序已绑定合格素材原文、修订及位置；已排除素材不进入证据池",
    )
    history: list[dict[str, Any]] = []

    async def checkpoint(guide: GuideDraft, **step: Any) -> None:
        history.append({**step, "guide": catalog.bind(guide).model_dump(mode="json")})
        await client.port.save(
            "construction_guides",
            list(history),
            stage="construction",
            message="本步指引与证据引用已验证，错误会在原步骤反馈修正",
        )

    guide_parts: list[GuideDraft] = []
    material_refs = tuple(catalog.materials)
    for start in range(0, len(material_refs), 16):
        payload = catalog.payload(material_refs[start : start + 16])
        allowed = tuple(e["ref"] for e in payload["evidence"])
        part = await client.ask(
            GuideDraft,
            COACH_ROLE_POLICY
            + "从本批素材提炼有边界的表达规律。规则只用 evidence_refs 选择输入证据编号，"
            "原文、素材 ID、修订由程序绑定，不输出或改写 quote。负面意见用于约束表达，"
            "不伪造期望回答，不把用户事实或专业结论提炼成风格。"
            "不要按数量投票；一条意见可产生局部规则，不冒充全局规律。"
            "只提炼有依据的规则，无法隔离的冲突不强行统一。",
            payload,
            stage="construction",
            validation=catalog.guide_validation(allowed),
        )
        guide_parts.append(part)
        await checkpoint(part, phase="extract", batch=start // 16 + 1)

    level = 0
    while len(guide_parts) > 1:
        level += 1
        merged: list[GuideDraft] = []
        for start in range(0, len(guide_parts), 6):
            batch = guide_parts[start : start + 6]
            allowed = catalog.used_refs(batch)
            guide = await client.ask(
                GuideDraft,
                COACH_ROLE_POLICY
                + "合并有依据的表达规律，收窄矛盾边界，不以多数取代少数的具体意见。"
                "每条规则只引用本次 evidence 中的编号，不能引入其他素材、"
                "测试题或新证据，不能输出 quote。保留有效边界，不制造新的表达规则。",
                {
                    "guides": [g.model_dump(mode="json") for g in batch],
                    "evidence": catalog.evidence_payload(allowed),
                },
                stage="construction",
                validation=catalog.guide_validation(allowed),
            )
            merged.append(guide)
            await checkpoint(guide, phase="merge", level=level, batch=start // 6 + 1)
        guide_parts = merged

    guide = guide_parts[0]
    eligible = catalog.eligible_examples(guide)
    if eligible:
        selection = await client.ask(
            ExampleSelection,
            COACH_ROLE_POLICY + "指引已经验证，不再改写指引。只从 eligible_examples 选取最小互补的"
            "固定表达示例集合，不固定数量，不选测试题，不新增示例或期望回答。"
            "example_ids 使用输入的短编号。explanation 说明选择理由。",
            {
                "guide": guide.model_dump(mode="json"),
                "eligible_examples": [
                    {
                        "id": ref,
                        "original_response": m.original_response,
                        "desired_response": m.desired_response,
                        "user_input": m.user_input,
                        "history": [item.model_dump(mode="json") for item in m.history],
                    }
                    for ref, m in eligible.items()
                ],
            },
            stage="construction",
            validation=catalog.selection_validation(guide, style_id, version_id, name),
        )
    else:
        selection = ExampleSelection(
            example_ids=(),
            explanation="已提炼表达指引，没有关联的合格正面表达对，不虚构示例",
        )
    package = catalog.package(guide, selection, style_id, version_id, name)
    await client.port.save(
        "draft",
        package.model_dump(mode="json"),
        stage="construction",
        message="风格包已由验证过的指引、程序绑定原文及选定示例组装完成",
    )
    return package, selection.explanation
