"""Style-scoped corpus and version lifecycle. No legacy A/B dependencies."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database

from .contracts import ExampleInput, ExampleState, ReviewInput, StyleInput
from .models import Example, ReviewCase, Style, Version


def data(row: Any) -> dict[str, Any]:
    result = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    if isinstance(row, Version):
        result["examples"] = [
            {k: v for k, v in e.items() if k not in {"embedding", "embedding_model"}}
            for e in row.examples
        ]
        result.pop("worker_token", None)
        result.pop("lease_until", None)
    return result


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def styles(self) -> list[dict[str, Any]]:
        async with self.database.session() as s:
            result = []
            for row in await s.scalars(select(Style).order_by(Style.created_at)):
                item = data(row)
                item["example_count"] = await s.scalar(
                    select(func.count()).select_from(Example).where(Example.style_id == row.id)
                )
                active = (
                    await s.get(Version, row.active_version_id) if row.active_version_id else None
                )
                item["active_version"] = active.name if active else None
                result.append(item)
            return result

    async def create_style(self, payload: StyleInput) -> dict[str, Any]:
        async with self.database.session() as s, s.begin():
            row = Style(**payload.model_dump())
            s.add(row)
            await s.flush()
            return data(row)

    async def examples(
        self,
        style_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        q: str = "",
        category: str = "",
        source: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        async with self.database.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            filters = [Example.style_id == style_id]
            if q:
                pattern = (
                    "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                )
                filters.append(
                    Example.id.ilike(pattern, escape="\\")
                    | Example.user_input.ilike(pattern, escape="\\")
                    | Example.original_response.ilike(pattern, escape="\\")
                    | Example.desired_response.ilike(pattern, escape="\\")
                )
            for key, value in (("category", category), ("source", source), ("status", status)):
                if value:
                    filters.append(getattr(Example, key) == value)
            total = await s.scalar(select(func.count()).select_from(Example).where(*filters))
            rows = (
                await s.scalars(
                    select(Example)
                    .where(*filters)
                    .order_by(Example.created_at.desc(), Example.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            versions = (await s.scalars(select(Version).where(Version.style_id == style_id))).all()
            items = []
            for row in rows:
                included = [
                    v.name
                    for v in versions
                    if any(e["id"] == row.id for e in v.snapshot.get("examples", []))
                ]
                items.append({**data(row), "included_versions": included})
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def append(self, style_id: str, payload: ExampleInput, actor: str) -> dict[str, Any]:
        async with self.database.session() as s, s.begin():
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            row = Example(style_id=style_id, **payload.model_dump(), actor=actor)
            s.add(row)
            await s.flush()
            return data(row)

    async def example_state(
        self, style_id: str, example_id: str, payload: ExampleState
    ) -> dict[str, Any]:
        async with self.database.session() as s, s.begin():
            row = await s.get(Example, example_id)
            if row is None or row.style_id != style_id:
                raise LookupError("示例不存在")
            row.status, row.reason = payload.status, payload.reason
            return data(row)

    async def versions(self, style_id: str) -> list[dict[str, Any]]:
        async with self.database.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            result = []
            for v in await s.scalars(
                select(Version)
                .where(Version.style_id == style_id)
                .order_by(Version.created_at.desc())
            ):
                rows = (
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == v.id))
                ).all()
                counts = {
                    "total": len(rows),
                    "accept": 0,
                    "reject": 0,
                    "pending": 0,
                    "auto_failed": sum(not r.automated.get("passed") for r in rows),
                }
                for row in rows:
                    counts[row.review["decision"] if row.review else "pending"] += 1
                result.append({**data(v), "review_summary": counts})
            return result

    async def build(self, style_id: str, actor: str) -> dict[str, Any]:
        try:
            async with self.database.session() as s, s.begin():
                style = await s.scalar(select(Style).where(Style.id == style_id).with_for_update())
                if style is None:
                    raise LookupError("风格不存在")
                if await s.scalar(
                    select(Version.id).where(
                        Version.style_id == style_id, Version.status.in_(["queued", "building"])
                    )
                ):
                    raise ValueError("已有构建任务正在进行")
                rows = (
                    await s.scalars(
                        select(Example)
                        .where(
                            Example.style_id == style_id,
                            Example.status.in_(["pending", "approved"]),
                        )
                        .order_by(Example.id)
                    )
                ).all()
                if not rows:
                    raise ValueError("请先追加至少一条纠正素材")
                # All eligible material, across all versions, is frozen in this transaction.
                examples = [
                    {k: v for k, v in data(row).items() if k != "created_at"} for row in rows
                ]
                review_rows = (
                    await s.scalars(
                        select(ReviewCase)
                        .join(Version)
                        .where(Version.style_id == style_id, ReviewCase.review.is_not(None))
                    )
                ).all()
                feedback = [
                    {
                        "example_id": r.example_id,
                        "doctor_response": r.doctor_response,
                        "review": r.review,
                    }
                    for r in review_rows
                ]
                base = (
                    await s.get(Version, style.active_version_id)
                    if style.active_version_id
                    else None
                )
                identifier = new_uuid()
                row = Version(
                    id=identifier,
                    style_id=style_id,
                    name=f"{style.name}-{utc_now():%Y%m%d%H%M%S}-{identifier[:6]}",
                    actor=actor,
                    snapshot={
                        "examples": examples,
                        "feedback": feedback,
                        "base_guide": base.guide if base else {},
                        "style_name": style.name,
                    },
                    events=[
                        {
                            "stage": "冻结示例库",
                            "message": f"已冻结 {len(examples)} 条素材和 {len(feedback)} 条评审",
                            "time": utc_now().isoformat(),
                        }
                    ],
                )
                s.add(row)
                await s.flush()
                return data(row)
        except IntegrityError as exc:
            raise ValueError("已有构建任务正在进行") from exc

    async def cases(
        self, style_id: str, version_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        async with self.database.session() as s:
            version = await s.get(Version, version_id)
            if version is None or version.style_id != style_id:
                raise LookupError("版本不存在")
            all_rows = (
                await s.scalars(
                    select(ReviewCase)
                    .where(ReviewCase.version_id == version_id)
                    .order_by(ReviewCase.id)
                )
            ).all()
            counts = {"pending": 0, "accept": 0, "reject": 0}
            for row in all_rows:
                counts[row.review["decision"] if row.review else "pending"] += 1
            return {
                "items": [data(r) for r in all_rows[offset : offset + limit]],
                "total": len(all_rows),
                "counts": counts,
                "limit": limit,
                "offset": offset,
            }

    async def review(
        self, style_id: str, case_id: str, payload: ReviewInput, actor: str
    ) -> dict[str, Any]:
        async with self.database.session() as s, s.begin():
            row = await s.get(ReviewCase, case_id)
            if row is None:
                raise LookupError("评审样例不存在")
            version = await s.scalar(
                select(Version).where(Version.id == row.version_id).with_for_update()
            )
            if version is None or version.style_id != style_id:
                raise LookupError("评审样例不存在")
            if version.status != "ready_for_review":
                raise ValueError("仅待评审版本可以评分；已发布版本不可修改")
            previous_desired = row.review.get("desired_response") if row.review else None
            row.review = {**payload.model_dump(), "actor": actor, "time": utc_now().isoformat()}
            positive = await s.scalar(
                select(Example).where(
                    Example.source_case_id == row.id, Example.source == "accepted_review"
                )
            )
            if payload.decision == "accept" and row.automated.get("passed"):
                if positive is None:
                    positive = Example(
                        style_id=style_id,
                        source="accepted_review",
                        source_case_id=row.id,
                        user_input=row.user_input,
                        original_response=row.original_response,
                        desired_response=row.doctor_response,
                        actor=actor,
                        status="approved",
                    )
                    s.add(positive)
                else:
                    positive.status = "approved"
            elif positive is not None:
                positive.status = "excluded"
            if payload.desired_response and payload.desired_response != previous_desired:
                s.add(
                    Example(
                        style_id=style_id,
                        user_input=row.user_input,
                        original_response=row.doctor_response,
                        desired_response=payload.desired_response,
                        actor=actor,
                        source="review",
                        source_case_id=row.id,
                    )
                )
            return data(row)

    async def action(self, style_id: str, version_id: str, action: str) -> dict[str, Any]:
        async with self.database.session() as s, s.begin():
            style = await s.scalar(select(Style).where(Style.id == style_id).with_for_update())
            version = await s.scalar(
                select(Version).where(Version.id == version_id).with_for_update()
            )
            if style is None or version is None or version.style_id != style_id:
                raise LookupError("版本不存在")
            if action == "publish":
                cases = (
                    await s.scalars(select(ReviewCase).where(ReviewCase.version_id == version_id))
                ).all()
                if (
                    version.status != "ready_for_review"
                    or not cases
                    or any(
                        not c.automated.get("passed")
                        or not c.review
                        or c.review["decision"] != "accept"
                        for c in cases
                    )
                ):
                    raise ValueError("需要自动评测通过且全部人工评审接受后才能发布")
                version.status = "published"
            elif action == "activate":
                if version.status != "published":
                    raise ValueError("只能启用已发布版本")
                style.active_version_id = version.id
            elif action == "retry":
                if version.status != "failed":
                    raise ValueError("只能重试失败构建")
                version.status, version.error = "queued", None
            else:
                raise ValueError("未知操作")
            return data(version)
