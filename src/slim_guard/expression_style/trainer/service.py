"""Offline trainer orchestration. Produces artifacts, never publishes or activates."""

from typing import Any

from slim_guard.expression_style.package import Guide, StylePackage, canonical, make_package

from .client import TrainingClient
from .construction import construct
from .contracts import Material, MaterialAnalysis, TestCase
from .datasets import prepare_suite
from .evaluation import evaluate, summarize
from .materials import analyze_materials


def reference_panel(
    materials: tuple[Material, ...], analyses: tuple[MaterialAnalysis, ...]
) -> list[dict[str, Any]]:
    """Freeze one compact human reference per observed expression pattern, before candidates."""
    by_id = {m.id: m for m in materials}
    result: list[dict[str, Any]] = []
    seen = set()
    for analysis in analyses:
        if analysis.role != "positive" or analysis.expression_rule in seen:
            continue
        m = by_id[analysis.example_id]
        value = {
            "id": m.id,
            "original": m.original_response,
            "desired": m.desired_response,
            "opinion": m.correction_opinion,
        }
        if len(canonical([*result, value]).encode()) > 12_000:
            continue
        seen.add(analysis.expression_rule)
        result.append(value)
    return result


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
        client = self.client
        materials = tuple(Material.model_validate(m) for m in snapshot["materials"])
        if not materials:
            raise ValueError("没有可分析素材")
        analyses = await analyze_materials(client, materials)
        if not any(a.role in {"positive", "negative"} for a in analyses):
            raise ValueError("没有可用表达素材，请查看逐条审查原因")
        references = reference_panel(materials, analyses)
        await client.port.save(
            "reference_panel",
            references,
            stage="materials",
            message=f"已冻结 {len(references)} 条人工表达参照；无参照时不自动宣称风格提升",
        )
        baseline = (
            StylePackage.model_validate(snapshot["baseline"])
            if snapshot.get("baseline")
            else (
                make_package(
                    snapshot["style_id"],
                    "baseline",
                    snapshot["style_name"],
                    Guide(summary="忠实原文、简洁自然的基础表达。"),
                )
            )
        )
        consumed = snapshot.get("consumed_tests", [])
        suite = await prepare_suite(client, materials, consumed)
        feedback = snapshot.get("feedback", [])
        regressions = tuple(
            TestCase.model_validate(f["case"])
            for f in feedback
            if f["review"]["decision"] == "reject"
        )
        # Holdout answers and results never enter construction or the development loop.
        best: StylePackage | None = None
        best_score: tuple[int, int, int] | None = None
        failures: list[dict[str, Any]] = []
        rounds: list[dict[str, Any]] = []
        for round_number in range(1, client.gateway.budget.max_rounds + 1):
            package, explanation = await construct(
                client,
                materials,
                analyses,
                style_id=snapshot["style_id"],
                version_id=run_id,
                name=snapshot["style_name"],
                feedback=feedback,
                previous=best,
                failures=failures,
            )
            await client.port.save(
                f"candidate:{round_number}",
                package.model_dump(mode="json"),
                stage="optimization",
                message=f"第 {round_number} 轮候选已冻结：{explanation}",
            )
            rows = await evaluate(
                client,
                package,
                baseline,
                suite.development,
                references,
                stage="optimization",
                label=f"development-{round_number}",
            )
            regression_rows = (
                await evaluate(
                    client,
                    package,
                    baseline,
                    regressions,
                    references,
                    stage="optimization",
                    label=f"regression-{round_number}",
                )
                if regressions
                else []
            )
            metrics = summarize([*rows, *regression_rows])
            score = (
                -metrics["candidate_failed"],
                metrics["candidate_better"] - metrics["baseline_better"],
                -len(package.compiled_prompt.encode()),
            )
            retained = best_score is None or score > best_score
            round_result = {
                "round": round_number,
                "metrics": metrics,
                "development_metrics": summarize(rows),
                "regression_metrics": summarize(regression_rows),
                "retained": retained,
                "explanation": explanation,
                "difference": difference(best or baseline, package),
                "package_hash": package.package_hash,
            }
            rounds.append(round_result)
            await client.port.save(
                "rounds",
                rounds,
                stage="optimization",
                message=f"第 {round_number} 轮：{'保留' if retained else '未保留'}候选，"
                f"检查失败 {metrics['candidate_failed']} 条",
            )
            if not retained:
                break
            unchanged = best is not None and best.compiled_prompt == package.compiled_prompt
            best, best_score = package, score
            failures = [
                r
                for r in [*rows, *regression_rows]
                if not r["candidate"]["passed"] or r["preference"] != "candidate"
            ]
            if unchanged or not failures:
                break
        if best is None:
            raise ValueError("未产生有效候选")
        await client.port.save(
            "selected",
            best.model_dump(mode="json"),
            stage="acceptance",
            message="已锁定最终候选，不再根据验收题调参",
        )
        final = await evaluate(
            client,
            best,
            baseline,
            suite.acceptance,
            references,
            stage="acceptance",
            label="acceptance",
        )
        metrics = summarize(final)
        retained_round = next(r for r in reversed(rounds) if r["retained"])
        if metrics["candidate_failed"] or retained_round["metrics"]["regressions"]:
            conclusion = "不建议采用"
        elif not references or metrics["uncertain"]:
            conclusion = "需要人工确认"
        elif metrics["candidate_better"] > metrics["baseline_better"]:
            conclusion = "建议采用"
        else:
            conclusion = "无明确提升"
        selected_ids = {e.id for e in best.examples}
        rule_ids = {e.example_id for r in best.guide.rules for e in r.evidence}
        material_results = [
            {
                **a.model_dump(mode="json"),
                "selected_example": a.example_id in selected_ids,
                "used_for_rule": a.example_id in rule_ids,
            }
            for a in analyses
        ]
        report = {
            "schema_version": "1",
            "package_hash": best.package_hash,
            "conclusion": conclusion,
            "metrics": metrics,
            "development": retained_round["metrics"],
            "regression": retained_round["regression_metrics"],
            "execution": {
                "model": snapshot["model"],
                "review_signature": snapshot["review_signature"],
                "budget": snapshot["budget"],
                "judge_independent_model": False,
            },
            "baseline_version": baseline.version_id,
            "rounds": rounds,
            "difference": difference(baseline, best),
            "reference_ids": [r["id"] for r in references],
            "feedback_outcomes": [
                r for r in material_results if r["example_id"].startswith("review:")
            ],
            "release_eligible": metrics["candidate_failed"] == 0
            and retained_round["metrics"]["candidate_failed"] == 0,
            "limitations": [
                "自动评测不替代人工验收",
                "小规模验收不保证所有场景均改善",
                "生成与审查是同模型的独立调用，仍有相关偏差；基线按本次配置重新评测",
            ],
        }
        await client.port.save("final_report", report, stage="complete", message=conclusion)
        return {
            "package": best.model_dump(mode="json"),
            "report": report,
            "cases": final,
            "materials": material_results,
        }
