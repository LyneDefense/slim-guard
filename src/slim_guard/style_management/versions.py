"""Final-candidate review/publication lifecycle; no generated-material feedback loop."""

from typing import Any

from sqlalchemy import select

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import StylePackage

from .contracts import ReviewInput
from .models import ReviewCase, Style, Version
from .views import row_data


def review_counts(rows: list[ReviewCase]) -> dict[str, int]:
    counts = {"total": len(rows), "pending": 0, "accept": 0, "reject": 0, "auto_failed": 0}
    for row in rows:
        counts[row.review["decision"] if row.review else "pending"] += 1
        counts["auto_failed"] += not row.automated.get("passed", False)
    return counts


class VersionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def versions(self, style_id: str) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            result = []
            for row in await s.scalars(
                select(Version)
                .where(Version.style_id == style_id)
                .order_by(Version.created_at.desc())
            ):
                cases = list(
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == row.id))
                )
                result.append({**row_data(row), "review_summary": review_counts(cases)})
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
                "items": [row_data(r) for r in rows[offset : offset + limit]],
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
            row.review = {
                **value.model_dump(),
                "actor": actor,
                "time": utc_now().isoformat(),
                "revision": (row.review or {}).get("revision", 0) + 1,
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
                cases = list(
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == version_id))
                )
                if (
                    row.status != "ready_for_review"
                    or not row.report.get("release_eligible")
                    or row.report.get("package_hash") != package.package_hash
                    or row.report.get("metrics", {}).get("total") != len(cases)
                    or len(cases) < 10
                    or any(
                        not c.automated.get("passed")
                        or c.automated.get("package_hash") != package.package_hash
                        or not c.review
                        or c.review["decision"] != "accept"
                        for c in cases
                    )
                ):
                    raise ValueError("需有效独立验收、自动检查及回归通过，且人评全部接受")
                row.status = "published"
            elif action == "activate":
                if row.status != "published":
                    raise ValueError("只能启用已发布版本")
                style.active_version_id = row.id
            else:
                raise ValueError("未知版本操作")
            return row_data(row)
