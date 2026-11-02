"""Build one expression package, preview it, and leave taste decisions to the human."""

from typing import Any

from slim_guard.expression_style.package import Guide, StylePackage, make_package
from slim_guard.group_chat import ROLE_POLICY_VERSION

from .client import TrainingClient
from .construction import construct
from .contracts import WORKFLOW_VERSION, Material, MaterialAnalysis, PreviewSuite
from .evaluation import evaluate, summarize
from .materials import analyze_materials
from .preview import replay_suite
from .role_scope import scope_preview


def difference(baseline: StylePackage, candidate: StylePackage) -> dict[str, Any]:
    before = {r.rule_id: r.model_dump(mode="json") for r in baseline.guide.rules}
    after = {r.rule_id: r.model_dump(mode="json") for r in candidate.guide.rules}
    return {
        "rules_added": [v for k, v in after.items() if k not in before],
        "rules_removed": [v for k, v in before.items() if k not in after],
        "rules_changed": [
            {"before": before[k], "after": v}
            for k, v in after.items()
            if k in before and before[k] != v
        ],
        "examples_before": [e.id for e in baseline.examples],
        "examples_after": [e.id for e in candidate.examples],
        "prompt_changed": baseline.compiled_prompt != candidate.compiled_prompt,
    }


class StyleTrainer:
    def __init__(self, client: TrainingClient) -> None:
        self.client = client

    async def run(self, snapshot: dict[str, Any], run_id: str) -> dict[str, Any]:
        if snapshot.get("workflow_version") != WORKFLOW_VERSION:
            raise ValueError("旧构建流程已停用，请新建构建；素材和评审反馈仍保留")
        client = self.client
        materials = tuple(Material.model_validate(m) for m in snapshot["materials"])
        if not materials:
            raise ValueError("没有可分析素材")
        baseline = (
            StylePackage.model_validate(snapshot["baseline"])
            if snapshot.get("baseline")
            else make_package(
                snapshot["style_id"],
                "baseline",
                snapshot["style_name"],
                Guide(summary="忠实原文、简洁自然的基础表达。"),
            )
        )
        if snapshot.get("edited_package"):
            package = StylePackage.model_validate(snapshot["edited_package"])
            analyses = tuple(MaterialAnalysis.model_validate(a) for a in snapshot["analyses"])
            explanation = "采用人工编辑的表达指引，保留来源版本的有效固定示例"
        else:
            analyses = await analyze_materials(client, materials)
            if not any(a.role in {"positive", "negative"} for a in analyses):
                raise ValueError("没有可用表达素材，请查看逐条审查原因")
            package, explanation = await construct(
                client,
                materials,
                analyses,
                style_id=snapshot["style_id"],
                version_id=run_id,
                name=snapshot["style_name"],
                feedback=snapshot.get("feedback", []),
            )
        suite = replay_suite(
            PreviewSuite.model_validate(snapshot["preview_suite"]),
            materials,
            analyses,
            snapshot.get("feedback", []),
        )
        package, suite, scope = await scope_preview(client, package, suite)
        await client.port.save(
            "selected",
            package.model_dump(mode="json"),
            stage="construction",
            message=explanation + "；已按固定教练职责筛选示例，不改变原稿含义",
        )
        await client.port.save(
            "preview_suite",
            suite.model_dump(mode="json"),
            stage="preview",
            message=f"已冻结 {len(suite.cases)} 条固定试答／人工样例回放；不宣称独立验收准确率",
        )
        rows = await evaluate(client, package, baseline, suite.cases)
        metrics = summarize(rows)
        selected_ids = {e.id for e in package.examples}
        rule_ids = {e.example_id for r in package.guide.rules for e in r.evidence}
        material_results = [
            {
                **a.model_dump(mode="json"),
                "selected_example": a.example_id in selected_ids,
                "used_for_rule": a.example_id in rule_ids,
            }
            for a in analyses
        ]
        report = {
            "schema_version": "2",
            "workflow_version": WORKFLOW_VERSION,
            "package_hash": package.package_hash,
            "suite_version": suite.version,
            "conclusion": "存在语义、职责或执行问题，暂不可发布"
            if metrics["content_failed"]
            else "试答完成，请人工确认语气",
            "preview_metrics": metrics,
            "release_eligible": metrics["content_failed"] == 0,
            "difference": difference(baseline, package),
            "baseline_version": baseline.version_id,
            "source_version_id": snapshot.get("source_version_id"),
            "role_policy_version": ROLE_POLICY_VERSION,
            "role_excluded_count": sum(not d["allowed"] for d in scope),
            "feedback_outcomes": [
                m for m in material_results if m["example_id"].startswith("review:")
            ],
            "limitations": [
                "这是表达指引和示例的构建，不修改模型参数",
                "固定试答与已知样例回放不是独立盲测，不代表泛化准确率",
                "自动表达建议仅供参考，是否符合你的语气由你确认",
                "语义、数字、事实与安全边界仍须通过检查；启用后线上继续审查",
            ],
        }
        await client.port.save(
            "final_report", report, stage="complete", message=report["conclusion"]
        )
        return {
            "package": package.model_dump(mode="json"),
            "report": report,
            "cases": rows,
            "materials": material_results,
        }
