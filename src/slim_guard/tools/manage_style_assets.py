"""Operator-only A/B import and reviewed asset publication; never auto-activates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from slim_guard.agents.contracts import CommunicationAct, ResponsePlan, StyledResponse
from slim_guard.db.session import Database
from slim_guard.style_corpus import StyleAssetBundle, StyleEvalCase, StyleEvalReport
from slim_guard.style_profiles import StyleProfileRepository
from slim_guard.style_reviews import (
    StyleABPairImport,
    StyleABReviewRepository,
    TrustedStyleABSource,
    style_ab_case_key,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def prepare_ab_import(
    data: Mapping[str, Any],
    *,
    bundle: StyleAssetBundle,
    actor: str,
    source_id: str,
    reviewed_inputs_confirmed: bool,
) -> tuple[tuple[StyleABPairImport, ...], TrustedStyleABSource]:
    """Validate exact generated/evaluated bytes before entering the ordinary admin UI."""
    if not reviewed_inputs_confirmed:
        raise ValueError("Explicit privacy and expression-asset review is required")
    bundle_hash = _digest(bundle.model_dump_json())
    evaluation = StyleEvalReport.model_validate(data.get("evaluation"))
    if data.get("bundle_sha256") != bundle_hash or evaluation.bundle_sha256 != bundle_hash:
        raise ValueError("A/B report does not match the exact bundle")
    if data.get("candidate_profile_version") != bundle.profile.version:
        raise ValueError("Candidate profile version mismatch")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > 60:
        raise ValueError("A/B report requires 1 to 60 generated cases")
    generated_hash = _digest(
        json.dumps(
            raw_cases,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if data.get("generated_cases_sha256") != generated_hash:
        raise ValueError("A/B generation payload checksum mismatch")
    evaluation_hash = _digest(evaluation.model_dump_json())
    results = {result.case_id: result for result in evaluation.results}
    if len(results) != len(evaluation.results) or len(results) != len(raw_cases):
        raise ValueError("Evaluation results do not cover the generated cases exactly")
    pairs: list[StyleABPairImport] = []
    evaluated_cases: list[StyleEvalCase] = []
    examples = {example.example_id: example for example in bundle.examples}
    for raw in raw_cases:
        if not isinstance(raw, dict) or raw.get("synthetic") is not True:
            raise ValueError("Only invented synthetic plans may enter ordinary administrator A/B")
        baseline, candidate = raw["baseline"], raw["candidate"]
        if any(item.get("generation_status") != "succeeded" for item in (baseline, candidate)):
            raise ValueError("Degraded generations cannot be imported as comparable A/B pairs")
        plan = ResponsePlan.model_validate(raw["response_plan"])
        response = StyledResponse.model_validate(candidate["response"])
        case = StyleEvalCase(
            case_id=raw["case_id"],
            response_plan=plan,
            styled_response=response,
            generation_status="succeeded",
        )
        result = results.get(case.case_id)
        if result is None or result.communication_act != plan.communication_act:
            raise ValueError("Generated case is not covered by its evaluation result")
        expected_examples = tuple(
            example.example_id
            for example in bundle.examples
            if example.communication_act == plan.communication_act
        )[:5]
        if tuple(candidate["used_example_ids"]) != expected_examples:
            raise ValueError("Generated example selection does not match the evaluated bundle")
        if any(example_id not in examples for example_id in candidate["used_example_ids"]):
            raise ValueError("Unrecognized candidate example ID")
        evaluated_cases.append(case)
        # Include the exact report digest: repeated evaluations are distinct immutable batches.
        pairs.append(
            StyleABPairImport(
                case_id=style_ab_case_key(evaluation_hash, case.case_id),
                source_sample_sha256=_digest(case.model_dump_json()),
                response_plan=plan,
                baseline_response=StyledResponse.model_validate(baseline["response"]),
                candidate_response=response,
                baseline_profile_version=data["baseline_profile_version"],
                candidate_profile_version=bundle.profile.version,
                baseline_generation_model=data["generation_model"],
                candidate_generation_model=data["generation_model"],
                baseline_example_ids=tuple(baseline["used_example_ids"]),
                candidate_example_ids=tuple(candidate["used_example_ids"]),
                candidate_bundle_sha256=bundle_hash,
                automated_evaluation_sha256=evaluation_hash,
                automated_judge_model=evaluation.model,
                automated_judge_status="passed"
                if (
                    result.passed
                    and result.judgment is not None
                    and result.judgment.passed
                    and result.failure_code is None
                )
                else "failed",
            )
        )
    case_hash = _digest(
        json.dumps(
            [case.model_dump() for case in evaluated_cases],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if case_hash != evaluation.cases_sha256:
        raise ValueError("Generated responses differ from the actually evaluated cases")
    required_acts = tuple(CommunicationAct)
    source = TrustedStyleABSource(
        source_id=source_id,
        manifest_sha256=StyleABReviewRepository.pairs_manifest_sha256(
            pairs,
            source_id=source_id,
            required_communication_acts=required_acts,
        ),
        imported_by=actor,
        required_communication_acts=required_acts,
        synthetic_confirmed=True,
        deidentified_confirmed=True,
        expression_assets_reviewed=True,
    )
    return tuple(pairs), source


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_reviewed_inputs:
        raise ValueError("--confirm-reviewed-inputs requires your actual privacy/expression review")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    prepared = None
    if args.command == "import-ab":
        bundle = StyleAssetBundle.model_validate_json(args.bundle.read_text(encoding="utf-8"))
        prepared = prepare_ab_import(
            payload,
            bundle=bundle,
            actor=args.actor,
            source_id=args.source_id,
            reviewed_inputs_confirmed=args.confirm_reviewed_inputs,
        )
    database = Database(args.database_url)
    try:
        await database.migrate()
        if prepared is not None:
            pairs, source = prepared
            ids = await StyleABReviewRepository(database).import_pairs(pairs, trusted_source=source)
            return {"status": "pending_human_scores", "case_ids": ids, "published": False}
        asset = await StyleProfileRepository(database).import_reviewed_bundle(
            payload,
            actor=args.actor,
            privacy_confirmed=True,
            expression_only_confirmed=True,
            evaluation_reviewed=args.confirm_evaluation_reviewed,
        )
        return {"status": "evaluated", "version": asset.version, "activated": False}
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="Explicit target application DB URL")
    parser.add_argument("--actor", required=True, help="Actual operator identifier")
    parser.add_argument("--confirm-reviewed-inputs", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-ab")
    importer.add_argument("--input", type=Path, required=True, help="Generated comparison JSON")
    importer.add_argument("--bundle", type=Path, required=True, help="Exact approved draft bundle")
    importer.add_argument("--source-id", required=True)
    publisher = commands.add_parser("publish")
    publisher.add_argument("--input", type=Path, required=True, help="Evaluated corpus export JSON")
    publisher.add_argument("--confirm-evaluation-reviewed", action="store_true")
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
