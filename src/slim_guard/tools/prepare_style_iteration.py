"""Freeze reviewed A/B decisions and named corrections for one new style version."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slim_guard.config import DatabaseSettings
from slim_guard.db.session import Database
from slim_guard.style_feedback import StyleCorrectionFeedbackRepository
from slim_guard.style_reviews import StyleABReviewRepository
from slim_guard.tools.style_asset_io import write_private_json


def _normalized_version(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError(f"{field} must contain 1 to 128 characters")
    return normalized


async def prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Build a private immutable input snapshot; never generate, publish, or activate."""

    if not args.confirm_reviewed_inputs:
        raise ValueError("--confirm-reviewed-inputs is required")
    actor = args.actor.strip()
    if not actor or len(actor) > 128:
        raise ValueError("A real operator actor is required")
    source_version = _normalized_version(
        args.source_profile_version, field="source_profile_version"
    )
    target_version = _normalized_version(
        args.target_profile_version, field="target_profile_version"
    )
    if source_version == target_version:
        raise ValueError("Target profile version must be new")

    database = Database(args.database_url)
    try:
        await database.migrate()
        reviews = StyleABReviewRepository(database)
        page = await reviews.list_cases(
            limit=10_000,
            offset=0,
            candidate_profile_version=source_version,
        )
        reviewed_cases: list[dict[str, Any]] = []
        pending_case_ids: list[str] = []
        for summary in page["items"]:
            detail = await reviews.get_case(summary["case_id"])
            if detail is None:
                raise RuntimeError("Style A/B case disappeared while preparing an iteration")
            review = detail["latest_human_review"]
            if review is None:
                pending_case_ids.append(detail["case_id"])
                continue
            review = {
                **review,
                "created_at": review["created_at"].isoformat(),
            }
            reviewed_cases.append(
                {
                    "case_id": detail["case_id"],
                    "case_key": detail["case_key"],
                    "source_sample_sha256": detail["source_sample_sha256"],
                    "scenario_sha256": detail["scenario_sha256"],
                    "scenario": detail["scenario"],
                    "response_plan_sha256": detail["response_plan_sha256"],
                    "response_plan": detail["response_plan"],
                    "communication_act": detail["communication_act"],
                    "candidate_response_sha256": detail["candidate"]["response_sha256"],
                    "candidate_response": detail["candidate"]["response"],
                    "latest_human_review": review,
                }
            )
        if pending_case_ids:
            raise ValueError(
                f"Complete all {len(pending_case_ids)} pending A/B reviews before iteration"
            )

        corrections: list[dict[str, Any]] = []
        offset = 0
        feedback = StyleCorrectionFeedbackRepository(database)
        while True:
            feedback_page = await feedback.list(
                limit=100,
                offset=offset,
                profile_version=source_version,
            )
            corrections.extend(feedback_page["items"])
            offset += len(feedback_page["items"])
            if offset >= feedback_page["total"] or not feedback_page["items"]:
                break
        rejection_count = sum(
            item["latest_human_review"]["decision"] == "reject"
            for item in reviewed_cases
        )
        if rejection_count == 0 and not corrections:
            raise ValueError("No rejected review or named correction requires a new version")

        sources = {
            "a_b_reviews": reviewed_cases,
            "style_corrections": corrections,
        }
        source_sha256 = hashlib.sha256(
            json.dumps(
                sources,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        return {
            "schema_version": "1",
            "status": "prepared_pending_profile_revision",
            "source_profile_version": source_version,
            "target_profile_version": target_version,
            "actor": actor,
            "prepared_at": datetime.now(UTC).isoformat(),
            "reviewed_inputs_confirmed": True,
            "source_sha256": source_sha256,
            "counts": {
                "reviewed_a_b_cases": len(reviewed_cases),
                "accepted_a_b_cases": len(reviewed_cases) - rejection_count,
                "rejected_a_b_cases": rejection_count,
                "style_corrections": len(corrections),
            },
            "sources": sources,
            "next_steps": [
                "Derive expression-only rules and examples without copying user facts.",
                "Generate the exact target bundle and scenario-bound regression cases.",
                "Run real-model generation and automated fidelity/style evaluation.",
                "Import the exact passing comparison for named A/B human review.",
            ],
            "published": False,
            "activated": False,
        }
    finally:
        await database.close()


def main() -> None:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--database-url",
        default=DatabaseSettings().database_url,
        help="Application database containing named reviews and corrections",
    )
    command.add_argument("--source-profile-version", required=True)
    command.add_argument("--target-profile-version", required=True)
    command.add_argument("--actor", required=True)
    command.add_argument("--confirm-reviewed-inputs", action="store_true")
    command.add_argument("--output", required=True, type=str)
    args = command.parse_args()
    result = asyncio.run(prepare(args))
    destination = write_private_json(result, Path(args.output))
    print(
        json.dumps(
            {
                "output": str(destination),
                "status": result["status"],
                "source_sha256": result["source_sha256"],
                "counts": result["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
