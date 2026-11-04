"""Final-candidate review/publication lifecycle; no generated-material feedback loop."""

from typing import Any

from sqlalchemy import select

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import StylePackage
from slim_guard.expression_style.trainer.contracts import WORKFLOW_VERSION
from slim_guard.group_chat import ROLE_POLICY_VERSION

from .contracts import ReviewInput
from .models import ReviewCase, Style, Version
from .preview_state import active_retry, latest_retry_summaries, output_revision
from .views import row_data


def review_counts(rows: list[ReviewCase]) -> dict[str, int]:
    counts = {"total": len(rows), "pending": 0, "accept": 0, "reject": 0, "auto_failed": 0}
    for row in rows:
        counts[row.review["decision"] if row.review else "pending"] += 1
        counts["auto_failed"] += not row.automated.get("passed", False) or bool(
            row.automated.get("fallback")
        )
    return counts


class VersionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def versions(self, style_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            result = []
            retries = await latest_retry_summaries(s, style_id)
            for row in await s.scalars(
                select(Version)
                .where(Version.style_id == style_id)
                .order_by(Version.created_at.desc())
            ):
                cases = list(
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == row.id))
                )
                result.append(
                    {
                        **row_data(row),
                        "review_summary": review_counts(cases),
                        "role_policy_current": row.report.get("role_policy_version")
                        == ROLE_POLICY_VERSION,
                        "preview_retry": retries.get(row.id),
                        "preview_policy_current": row.report.get("workflow_version")
                        == WORKFLOW_VERSION,
                    }
                )
            return result

    async def cases(
        self, style_id: str, version_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        async with self.db.session() as s:
            version = await s.get(Version, version_id)
            if version is None or version.style_id != style_id:
                raise LookupError("版本不存在")
            rows = list(
                await s.scalars(
                    select(ReviewCase)
                    .where(ReviewCase.version_id == version_id)
                    .order_by(ReviewCase.id)
                )
            )
            return {
                "items": [
                    {**row_data(r), "output_revision": output_revision(r)}
                    for r in rows[offset : offset + limit]
                ],
                "total": len(rows),
                "limit": limit,
                "offset": offset,
                "counts": review_counts(rows),
            }

    async def review(
        self, style_id: str, case_id: str, value: ReviewInput, actor: str
    ) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            row = await s.get(ReviewCase, case_id)
            if row is None:
                raise LookupError("评审用例不存在")
            version = await s.scalar(
                select(Version).where(Version.id == row.version_id).with_for_update()
            )
            if version is None or version.style_id != style_id:
                raise LookupError("评审用例不存在")
            if version.status != "ready_for_review":
                raise ValueError("已发布版本只读")
            job = await active_retry(s, style_id, version.id)
            if job and any(
                item["case_id"] == row.id for item in job.snapshot.get("retry_cases", [])
            ):
                raise ValueError("本条正在重试，请等待新回答后再评审")
            revision = output_revision(row)
            if value.output_revision != revision and (
                value.output_revision is not None or revision > 1
            ):
                raise ValueError("回答已更新，请刷新后重新评审")
            row.review = {
                **value.model_dump(),
                "output_revision": revision,
                "actor": actor,
                "time": utc_now().isoformat(),
                "revision": max(
                    (row.review or {}).get("revision", 0),
                    row.automated.get("last_review_revision", 0),
                )
                + 1,
            }
            return row_data(row)

    async def action(self, style_id: str, version_id: str, action: str) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            style = await s.scalar(select(Style).where(Style.id == style_id).with_for_update())
            row = await s.scalar(select(Version).where(Version.id == version_id).with_for_update())
            if style is None or row is None or row.style_id != style_id:
                raise LookupError("版本不存在")
            package = StylePackage.model_validate(row.package)
            if package.style_id != style_id or package.version_id != version_id:
                raise ValueError("风格产物归属不匹配")
            if action == "publish":
                if await active_retry(s, style_id, version_id):
                    raise ValueError("失败用例正在重试，完成并重新评审后才能发布")
                if row.report.get("role_policy_version") != ROLE_POLICY_VERSION:
                    raise ValueError("此版本尚未按当前教练职责检查，请重新构建后评审")
                if row.report.get("workflow_version") != WORKFLOW_VERSION:
                    raise ValueError("此版本仍使用旧改写试答，请重新构建；已有反馈和已发布版本保留")
                cases = list(
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == version_id))
                )
                is_preview = row.report.get("schema_version") == "2"
                metrics = row.report.get("preview_metrics" if is_preview else "metrics", {})
                if (
                    row.status != "ready_for_review"
                    or not row.report.get("release_eligible")
                    or row.report.get("package_hash") != package.package_hash
                    or metrics.get("total") != len(cases)
                    or (len(cases) < (1 if is_preview else 10))
                    or any(
                        not c.automated.get("passed")
                        or c.automated.get("fallback")
                        or c.automated.get("package_hash") != package.package_hash
                        or not c.review
                        or c.review["decision"] != "accept"
                        or c.review.get("output_revision", 1) != output_revision(c)
                        for c in cases
                    )
                ):
                    raise ValueError("需完整试答及语义检查通过，且人工逐条确认语气后发布")
                row.status = "published"
            elif action == "activate":
                if row.status != "published":
                    raise ValueError("只能启用已发布版本")
                style.active_version_id = row.id
            else:
                raise ValueError("未知版本操作")
            return row_data(row)
