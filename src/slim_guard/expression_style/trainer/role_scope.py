"""Semantic role-scoping for preview sources and inherited expression examples."""

from typing import Any

from pydantic import Field

from slim_guard.expression_style.package import Artifact, StylePackage, make_package
from slim_guard.group_chat import COACH_ROLE_POLICY

from .client import TrainingClient
from .contracts import PreviewSuite
from .output_validation import OutputValidation, reference_coverage
from .references import BatchReferences


class CoachScopeDecision(Artifact):
    source_id: str
    allowed: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=1000)


class CoachScopeBatch(Artifact):
    items: tuple[CoachScopeDecision, ...]


def scope_validation(refs: BatchReferences) -> OutputValidation[CoachScopeBatch]:
    def check(result: CoachScopeBatch) -> list[dict[str, Any]]:
        return reference_coverage(
            list(refs.references), [d.source_id for d in result.items], field="source_id"
        )

    return OutputValidation(
        version="coach-scope-coverage-v1", references=refs.references, check=check
    )


async def scope_preview(
    client: TrainingClient,
    package: StylePackage,
    suite: PreviewSuite,
) -> tuple[StylePackage, PreviewSuite, list[dict[str, Any]]]:
    # A user can ask for an operation and still receive doctor commentary.
    # Scope only the approved assistant examples, never filter test inputs based
    # on an old neutral draft that is not used by direct generation.
    sources: list[dict[str, Any]] = [
        {
            "source_id": f"example:{e.id}",
            "source_kind": "example",
            "user_input": e.user_input,
            "dialogue": [m.model_dump(mode="json") for m in e.dialogue],
            "desired_text": e.desired_response,
        }
        for e in package.examples
    ]
    decisions: list[dict[str, Any]] = []
    for start in range(0, len(sources), 16):
        batch = sources[start : start + 16]
        refs = BatchReferences.for_ids([s["source_id"] for s in batch])
        review = await client.ask(
            CoachScopeBatch,
            COACH_ROLE_POLICY + "逐条检查以下固定对话中医生的表达是否属于教练职责，"
            "source_id 是本批短编号，必须原样完整覆盖每个编号，不得重复或新增。"
            "只审查角色归属，不评分语气或改写句子。"
            "期望回答或 dialogue 中助手发言含系统记录操作、状态播报或业务追问时 allowed=false，"
            "即使是历史素材或已被人工接受，也不能据此改变固定职责。"
            "正常点评可以引用已有记录；自然质疑异常陈述不等于代办记录或确认业务字段。"
            "用户提到记录操作不是医生越权；不因缺少真实工具执行而否定正常点评。"
            "不从单个词判断归属，说明实际承担了什么职责。",
            {
                "sources": [
                    {**s, "source_id": reference}
                    for reference, s in zip(refs.references, batch, strict=True)
                ]
            },
            stage="scope",
            validation=scope_validation(refs),
        )
        by_id = {
            refs.resolve(d.source_id): d.model_copy(update={"source_id": refs.resolve(d.source_id)})
            for d in review.items
        }
        decisions.extend({**s, **by_id[s["source_id"]].model_dump(mode="json")} for s in batch)
        await client.port.save(
            "coach_scope",
            decisions,
            stage="scope",
            message=f"已检查 {len(decisions)} 个原稿／示例；系统职责内容不进入医生风格试答",
        )
    allowed = {d["source_id"] for d in decisions if d["allowed"]}
    examples = tuple(e for e in package.examples if f"example:{e.id}" in allowed)
    if examples != package.examples:
        package = make_package(
            package.style_id, package.version_id, package.display_name, package.guide, examples
        )
    return package, suite, decisions
