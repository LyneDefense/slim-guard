"""Import an already generated Style Profile iteration without any model call."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from slim_guard.db.models import StyleABEvaluationCaseRecord
from slim_guard.db.session import Database
from slim_guard.style_corpus import StyleAssetBundle, StyleEvalReport
from slim_guard.style_evaluation import StyleEvaluationInput
from slim_guard.style_iteration_sources import style_iteration_digest
from slim_guard.style_iterations import StyleIterationRepository
from slim_guard.tools.manage_style_assets import prepare_ab_import


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = _read(args.input_snapshot)
    bundle = StyleAssetBundle.model_validate(_read(args.bundle))
    cases = tuple(StyleEvaluationInput.model_validate(item) for item in _read(args.cases))
    comparison = _read(args.comparison)
    if bundle.profile.version != snapshot.get("target_profile_version"):
        raise ValueError("Historical snapshot and bundle target versions differ")
    if comparison.get("candidate_profile_version") != bundle.profile.version:
        raise ValueError("Historical comparison belongs to another profile version")
    pairs, _trusted_source = prepare_ab_import(
        comparison,
        bundle=bundle,
        actor=args.actor,
        source_id=f"style-iteration-history:{bundle.profile.version}",
        reviewed_inputs_confirmed=args.confirm_exact_existing_artifacts,
    )
    comparison_cases = {item["case_id"]: item["response_plan"] for item in comparison["cases"]}
    if {case.case_id: case.response_plan.model_dump(mode="json") for case in cases} != (
        comparison_cases
    ):
        raise ValueError("Historical regression inputs do not match comparison plans")
    case_ids_by_act: dict[str, list[str]] = {}
    for case in cases:
        case_ids_by_act.setdefault(case.response_plan.communication_act.value, []).append(
            case.case_id
        )
    classifications = [
        {
            "source_kind": "ab_review",
            "source_id": item["latest_human_review"]["review_id"],
            "classification": "style_existing_act",
            "primary_act": item["communication_act"],
            "summary": "历史实名 A/B 结论，作为下一版本回归约束。",
            "generalized_rule": item["latest_human_review"].get("comment"),
            "derived_case_ids": case_ids_by_act[item["communication_act"]],
        }
        for item in snapshot["sources"]["a_b_reviews"]
    ]
    if snapshot["sources"]["style_corrections"]:
        raise ValueError("Historical corrections require explicit saved classifications")
    evaluation = StyleEvalReport.model_validate(comparison["evaluation"])
    database = Database(args.database_url)
    try:
        await database.migrate()
        async with database.session() as session:
            existing = tuple(
                await session.scalars(
                    select(StyleABEvaluationCaseRecord).where(
                        StyleABEvaluationCaseRecord.candidate_bundle_sha256
                        == comparison["bundle_sha256"],
                        StyleABEvaluationCaseRecord.automated_evaluation_sha256
                        == hashlib.sha256(evaluation.model_dump_json().encode()).hexdigest(),
                        StyleABEvaluationCaseRecord.candidate_profile_version
                        == bundle.profile.version,
                    )
                )
            )
        expected = {pair.case_id: pair.source_sample_sha256 for pair in pairs}
        actual = {row.case_key: row.source_sample_sha256 for row in existing}
        if actual != expected:
            raise ValueError("Historical A/B rows are missing or differ from the exact comparison")
        case_ids = tuple(row.id for row in existing)
        case_values = [case.model_dump(mode="json") for case in cases]
        run_result = await StyleIterationRepository(database).import_historical_run(
            source_version=snapshot["source_profile_version"],
            target_version=bundle.profile.version,
            actor=args.actor,
            input_snapshot=snapshot,
            classifications=classifications,
            profile=bundle.profile.model_dump(mode="json"),
            bundle=bundle.model_dump(mode="json"),
            cases=case_values,
            comparison=comparison,
            profile_sha256=hashlib.sha256(bundle.profile.model_dump_json().encode()).hexdigest(),
            bundle_sha256=hashlib.sha256(bundle.model_dump_json().encode()).hexdigest(),
            cases_sha256=style_iteration_digest(case_values),
            evaluation_sha256=hashlib.sha256(evaluation.model_dump_json().encode()).hexdigest(),
            generation_model=comparison["generation_model"],
            judge_model=evaluation.model,
        )
        return {
            "status": "ready_for_review",
            "run_id": run_result["run_id"],
            "target_version": run_result["target_version"],
            "case_ids": case_ids,
            "model_called": False,
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--input-snapshot", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--confirm-exact-existing-artifacts", action="store_true")
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
