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
            "逐条筛选表达风格迁移素材，严格覆盖每个 ID。比较原回答和期望回答的差异，"
            "不是重新核查原回答中的业务事实或医疗知识。"
            "positive 表示可用的风格改写对：只调整语气、口吻、句式、口语程度或冗余客套，"
            "事实、建议、对象、状态、条件和风险保持一致；不要求逐字相同。"
            "删去无实质信息的礼貌寒暄也可为 positive，表达风格调整本身绝不是 content_change。"
            "negative 表示只有纠正意见、没有可用期望回答，可提炼表达限制而不伪造正面答案。"
            "content_change 仅表示实际新增、删除或改变业务事实、建议、判断或角色职责，"
            "必须指出具体改变了哪项内容；不能把口吻变化当成内容变化。"
            "原输出已经答非所问才是 semantic_drift；确实无法比较含义时才标上下文不足，不猜测。"
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
