"""Classify all material revisions, retaining exclusion reasons and counterexamples."""

from .client import TrainingClient
from .contracts import AnalysisBatch, Material, MaterialAnalysis


async def analyze_materials(
    client: TrainingClient,
    materials: tuple[Material, ...],
) -> tuple[MaterialAnalysis, ...]:
    results = []
    seen: dict[tuple[str, str, str], str] = {}
    for start in range(0, len(materials), 8):
        batch = materials[start : start + 8]
        parsed = await client.ask(
            AnalysisBatch,
            "逐条分析纠正素材，严格覆盖每个 ID。positive 仅用于含义相同的表达对；"
            "只有负面意见可为 negative；新增事实、建议是 content_change；"
            "原输出已答非所问是 semantic_drift；上下文不足不要猜测。"
            "保留表达倾向及边界，不能按询问/鼓励等意图分类。输入数量多不代表质量高。",
            [m.model_dump(mode="json") for m in batch],
            stage="materials",
        )
        if {a.example_id for a in parsed.items} != {m.id for m in batch} or (
            len(parsed.items) != len(batch)
        ):
            raise ValueError("素材分析缺失或重复")
        indexed = {a.example_id: a for a in parsed.items}
        for material in batch:
            item = indexed[material.id]
            key = (
                material.original_response.strip(),
                material.desired_response.strip(),
                material.correction_opinion.strip(),
            )
            if key in seen:
                item = MaterialAnalysis(
                    example_id=material.id,
                    role="duplicate",
                    duplicate_of=seen[key],
                    reason="与已有表达素材重复",
                )
            else:
                seen[key] = material.id
            if item.role == "positive" and not material.desired_response:
                raise ValueError("只有纠正意见的素材不能作为正面表达对")
            results.append(item)
        await client.port.save(
            "materials",
            [r.model_dump(mode="json") for r in results],
            stage="materials",
            message=f"素材审查 {len(results)}／{len(materials)}",
        )
    return tuple(results)
