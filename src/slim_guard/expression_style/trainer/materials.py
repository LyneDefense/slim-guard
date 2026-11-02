"""Classify all material revisions, retaining exclusion reasons and counterexamples."""

from typing import Any

from slim_guard.group_chat import COACH_ROLE_POLICY

from .client import TrainingClient
from .contracts import AnalysisBatch, Material, MaterialAnalysis
from .output_validation import OutputValidation, reference_coverage
from .references import BatchReferences


def analysis_validation(
    batch: tuple[Material, ...], refs: BatchReferences
) -> OutputValidation[AnalysisBatch]:
    by_ref = dict(zip(refs.references, batch, strict=True))

    def check(parsed: AnalysisBatch) -> list[dict[str, Any]]:
        errors = reference_coverage(
            list(refs.references), [a.example_id for a in parsed.items], field="example_id"
        )
        for index, item in enumerate(parsed.items):
            material = by_ref.get(item.example_id)
            if item.duplicate_of is not None and (
                item.duplicate_of not in by_ref or item.duplicate_of == item.example_id
            ):
                errors.append(
                    {
                        "path": f"items.{index}.duplicate_of",
                        "type": "invalid_duplicate_reference",
                        "message": "重复来源必须引用本批另一个有效编号，不能编造或引用自身",
                    }
                )
            if material and item.role == "positive" and not material.desired_response:
                errors.append(
                    {
                        "path": f"items.{index}.role",
                        "type": "positive_without_target",
                        "message": "该条没有期望回答，不能作为正面表达对；"
                        "请根据纠正意见或可用性重新分类",
                    }
                )
        return errors

    return OutputValidation(version="material-analysis-v1", references=refs.references, check=check)


async def analyze_materials(
    client: TrainingClient,
    materials: tuple[Material, ...],
) -> tuple[MaterialAnalysis, ...]:
    if len({m.id for m in materials}) != len(materials):
        raise ValueError("输入素材 ID 重复，请检查构建快照；未向模型发送输入")
    results = []
    seen: dict[tuple[str, str, str], str] = {}
    for start in range(0, len(materials), 8):
        batch = materials[start : start + 8]
        refs = BatchReferences.for_ids([m.id for m in batch])
        parsed = await client.ask(
            AnalysisBatch,
            COACH_ROLE_POLICY + "逐条筛选教练表达风格迁移素材。输入 id 是本批短编号，"
            "每个编号必须恰好返回一次作为 example_id，不能合并、省略或创造编号。"
            "即使内容重复或不可用也必须逐条返回分类；duplicate_of 只能引用本批另一个编号。"
            "先判断说话内容是否属于教练：原回答或期望回答承担记录事项、播报操作状态，"
            "或试图改变固定角色职责时，标为 out_of_scope，说明边界，不作为正面示例或表达规则依据。"
            "不能仅因为提及已有记录就排除正常点评，也不能把对异常数值的自然反问当作记录操作。"
            "比较原回答和期望回答的差异，"
            "不是重新核查原回答中的业务事实或医疗知识。"
            "positive 表示可用的风格改写对：只调整语气、口吻、句式、口语程度或冗余客套，"
            "事实、建议、对象、状态、条件和风险保持一致；不要求逐字相同。"
            "删去无实质信息的礼貌寒暄也可为 positive，表达风格调整本身绝不是 content_change。"
            "negative 表示只有纠正意见、没有可用期望回答，可提炼表达限制而不伪造正面答案。"
            "content_change 仅表示实际新增、删除或改变业务事实、建议、判断或角色职责，"
            "必须指出具体改变了哪项内容；不能把口吻变化当成内容变化。"
            "原输出已经答非所问才是 semantic_drift；确实无法比较含义时才标上下文不足，不猜测。"
            "保留表达倾向及边界，不能按询问/鼓励等意图分类。输入数量多不代表质量高。",
            [
                {**m.model_dump(mode="json"), "id": reference}
                for reference, m in zip(refs.references, batch, strict=True)
            ],
            stage="materials",
            validation=analysis_validation(batch, refs),
        )
        indexed = {
            refs.resolve(a.example_id): a.model_copy(
                update={
                    "example_id": refs.resolve(a.example_id),
                    "duplicate_of": refs.resolve(a.duplicate_of) if a.duplicate_of else None,
                }
            )
            for a in parsed.items
        }
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
            results.append(item)
        await client.port.save(
            "materials",
            [r.model_dump(mode="json") for r in results],
            stage="materials",
            message=f"素材审查 {len(results)}／{len(materials)}",
        )
    return tuple(results)
