"""Identity of a reviewed answer and style-scoped preview job projections."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.expression_style.package import content_hash

from .models import BuildRun, ReviewCase


def output_revision(case: ReviewCase) -> int:
    return int(case.automated.get("output_revision", 1))


def case_fingerprint(case: ReviewCase) -> str:
    return content_hash(
        {
            "test_case": case.test_case,
            "user_input": case.user_input,
            "original_response": case.original_response,
            "doctor_response": case.doctor_response,
            "automated": case.automated,
            "review": case.review,
        }
    )


async def active_retry(session: AsyncSession, style_id: str, version_id: str) -> BuildRun | None:
    result: BuildRun | None = await session.scalar(
        select(BuildRun).where(
            BuildRun.style_id == style_id,
            BuildRun.status.in_(["queued", "running"]),
            BuildRun.snapshot["retry_version_id"].as_string() == version_id,
        )
    )
    return result


async def latest_retry_summaries(session: AsyncSession, style_id: str) -> dict[str, dict[str, Any]]:
    # Progress polling must not load all historical prompts, answers and checkpoints.
    rows = await session.execute(
        select(
            BuildRun.id,
            BuildRun.status,
            BuildRun.error,
            BuildRun.snapshot["retry_version_id"].as_string().label("version_id"),
            BuildRun.snapshot["target_count"].as_integer().label("target_count"),
        )
        .where(
            BuildRun.style_id == style_id,
            BuildRun.snapshot["job_kind"].as_string() == "preview_retry",
        )
        .order_by(BuildRun.created_at.desc(), BuildRun.id.desc())
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result.setdefault(
            row.version_id,
            {
                "id": row.id,
                "status": row.status,
                "error": row.error,
                "target_count": row.target_count,
            },
        )
    return result
