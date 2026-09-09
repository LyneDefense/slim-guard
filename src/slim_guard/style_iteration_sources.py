"""Freeze exact, reviewed inputs for a new Style Profile candidate."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from slim_guard.db.session import Database
from slim_guard.style_feedback import StyleCorrectionFeedbackRepository
from slim_guard.style_reviews import StyleABReviewRepository


def style_iteration_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


async def collect_style_iteration_sources(
    database: Database,
    *,
    source_profile_version: str,
) -> dict[str, Any]:
    """Return a deterministic snapshot of latest A/B reviews and corrections."""

    reviews = StyleABReviewRepository(database)
    page = await reviews.list_cases(
        limit=10_000,
        offset=0,
        candidate_profile_version=source_profile_version,
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
        normalized_review = {
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
                "latest_human_review": normalized_review,
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
            profile_version=source_profile_version,
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
    return {
        "source_profile_version": source_profile_version,
        "source_sha256": style_iteration_digest(sources),
        "counts": {
            "reviewed_a_b_cases": len(reviewed_cases),
            "accepted_a_b_cases": len(reviewed_cases) - rejection_count,
            "rejected_a_b_cases": rejection_count,
            "style_corrections": len(corrections),
        },
        "sources": sources,
    }


__all__ = ["collect_style_iteration_sources", "style_iteration_digest"]
