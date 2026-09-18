"""Freeze all material revisions and expose bounded, style-scoped build views."""

from typing import Any

from sqlalchemy import func, select

from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import content_hash
from slim_guard.expression_style.review.policy import review_signature
from slim_guard.expression_style.trainer.contracts import BuildBudget, Material

from .models import BuildRun, Example, ReviewCase, Style, Version
from .views import build_summary


class BuildRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def build(
        self,
        style_id: str,
        actor: str,
        *,
        model: str,
        request_key: str | None = None,
        budget: BuildBudget | None = None,
    ) -> dict[str, Any]:
        key = content_hash({"style_id": style_id, "request_key": request_key or new_uuid()})
        async with self.db.session() as s, s.begin():
            style = await s.scalar(select(Style).where(Style.id == style_id).with_for_update())
            if style is None:
                raise LookupError("风格不存在")
            previous = await s.scalar(select(BuildRun).where(BuildRun.request_key == key))
            if previous is not None:
                return build_summary(previous)
            if await s.scalar(
                select(BuildRun.id).where(
                    BuildRun.style_id == style_id, BuildRun.status.in_(["queued", "running"])
                )
            ):
                raise ValueError("该风格已有构建正在进行")
            materials = list(
                await s.scalars(
                    select(Example).where(Example.style_id == style_id).order_by(Example.id)
                )
            )
            if not materials:
                raise ValueError("请先追加纠正素材")
            cases = list(
                await s.scalars(
                    select(ReviewCase).join(Version).where(Version.style_id == style_id)
                )
            )
            baseline = (
                await s.get(Version, style.active_version_id) if style.active_version_id else None
            )
            run_id = new_uuid()
            output_feedback = [
                c for c in cases if c.review and c.review.get("concern", "output") == "output"
            ]
            feedback_materials = [
                Material(
                    id=f"review:{c.id}",
                    revision=c.review.get("revision", 1),
                    user_input=c.user_input,
                    original_response=c.doctor_response,
                    desired_response=c.review.get("desired_response", ""),
                    correction_opinion=c.review.get("reason", ""),
                ).model_dump(mode="json")
                for c in output_feedback
                if c.review and (c.review.get("desired_response") or c.review.get("reason"))
            ]
            snapshot = {
                "schema_version": "1",
                "style_id": style_id,
                "style_name": style.name,
                "materials": [
                    Material.model_validate(
                        {k: getattr(m, k) for k in Material.model_fields}
                    ).model_dump(mode="json")
                    for m in materials
                ]
                + feedback_materials,
                "library_count": len(materials),
                "unused_count": sum(m.revision != m.processed_revision for m in materials),
                "human_feedback_count": len(feedback_materials),
                "feedback": [
                    {"case": c.test_case, "doctor_response": c.doctor_response, "review": c.review}
                    for c in output_feedback
                    if c.review
                ],
                "evaluation_objections": [
                    {"case": c.test_case, "review": c.review}
                    for c in cases
                    if c.review and c.review.get("concern", "output") != "output"
                ],
                "consumed_tests": [c.test_case for c in cases],
                "baseline": baseline.package if baseline else {},
                "model": model,
                "review_signature": review_signature(model),
                "budget": (budget or BuildBudget()).model_dump(mode="json"),
            }
            row = BuildRun(
                id=run_id,
                style_id=style_id,
                actor=actor,
                request_key=key,
                name=f"{style.name}-{utc_now():%Y%m%d%H%M%S}-{run_id[:6]}",
                snapshot=snapshot,
                activity={"state": "queued", "message": f"已冻结 {len(materials)} 条素材"},
                progress_at=utc_now(),
                events=[
                    {
                        "sequence": 1,
                        "stage": "freeze",
                        "state": "completed",
                        "message": f"已冻结全库 {len(materials)} 条素材",
                        "time": utc_now().isoformat(),
                    }
                ],
            )
            s.add(row)
            await s.flush()
            return build_summary(row)

    async def runs(self, style_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        async with self.db.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            where = BuildRun.style_id == style_id
            total = await s.scalar(select(func.count()).select_from(BuildRun).where(where))
            rows = await s.scalars(
                select(BuildRun)
                .where(where)
                .order_by(BuildRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return {
                "items": [build_summary(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def detail(self, style_id: str, run_id: str) -> dict[str, Any]:
        async with self.db.session() as s:
            row = await s.get(BuildRun, run_id)
            if row is None or row.style_id != style_id:
                raise LookupError("构建不存在")
            keys = [k for k in row.checkpoints if not k.startswith(("model:", "rewrite:"))]
            return {**build_summary(row), "artifacts": ["input_materials", *keys]}

    async def events(
        self, style_id: str, run_id: str, after: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        async with self.db.session() as s:
            row = await s.get(BuildRun, run_id)
            if row is None or row.style_id != style_id:
                raise LookupError("构建不存在")
            return {
                "items": row.events[after : after + limit],
                "total": len(row.events),
                "next_sequence": min(after + limit, len(row.events)),
            }

    async def artifact(
        self, style_id: str, run_id: str, key: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        async with self.db.session() as s:
            row = await s.get(BuildRun, run_id)
            if row is None or row.style_id != style_id:
                raise LookupError("构建不存在")
            if key.startswith(("model:", "rewrite:")):
                raise LookupError("产物不存在")
            value = (
                row.snapshot["materials"] if key == "input_materials" else row.checkpoints.get(key)
            )
            if value is None:
                raise LookupError("产物尚未生成")
            if isinstance(value, list):
                if key == "materials":
                    materials = {m["id"]: m for m in row.snapshot["materials"]}
                    value = [{**materials.get(item["example_id"], {}), **item} for item in value]
                return {
                    "items": value[offset : offset + limit],
                    "total": len(value),
                    "offset": offset,
                    "limit": limit,
                }
            return {"value": value}

    async def action(self, style_id: str, run_id: str, action: str) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            await s.scalar(select(Style).where(Style.id == style_id).with_for_update())
            row = await s.scalar(select(BuildRun).where(BuildRun.id == run_id).with_for_update())
            if row is None or row.style_id != style_id:
                raise LookupError("构建不存在")
            if action == "cancel" and row.status in {"queued", "running"}:
                row.status, row.finished_at = "cancelled", utc_now()
            elif action == "resume" and row.status in {"failed", "cancelled"}:
                if await s.scalar(
                    select(BuildRun.id).where(
                        BuildRun.style_id == style_id,
                        BuildRun.status.in_(["queued", "running"]),
                        BuildRun.id != run_id,
                    )
                ):
                    raise ValueError("已有其他构建正在进行")
                row.status, row.error, row.finished_at = "queued", None, None
            else:
                raise ValueError("当前状态不支持该操作")
            row.worker_token, row.lease_until = None, None
            row.activity = {
                "state": row.status,
                "message": "已取消" if action == "cancel" else "等待恢复",
            }
            row.events = [
                *row.events,
                {
                    "sequence": len(row.events) + 1,
                    "stage": row.stage,
                    "state": row.status,
                    "time": utc_now().isoformat(),
                    "message": row.activity["message"],
                },
            ]
            return build_summary(row)
