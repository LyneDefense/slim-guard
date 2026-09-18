"""Human-authored material revisions and participation views."""

from typing import Any

from sqlalchemy import func, select

from slim_guard.db.session import Database

from .contracts import ExampleInput, StyleInput
from .models import Example, Style, Version
from .views import row_data


class CorpusRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def styles(self) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            items = []
            for style in await s.scalars(select(Style).order_by(Style.created_at)):
                active = (
                    await s.get(Version, style.active_version_id)
                    if style.active_version_id
                    else None
                )
                items.append(
                    {
                        **row_data(style),
                        "active_version": active.name if active else None,
                        "example_count": await s.scalar(
                            select(func.count())
                            .select_from(Example)
                            .where(Example.style_id == style.id)
                        ),
                    }
                )
            return items

    async def create_style(self, value: StyleInput) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            row = Style(**value.model_dump())
            s.add(row)
            await s.flush()
            return row_data(row)

    async def examples(
        self,
        style_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        q: str = "",
        participation: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        async with self.db.session() as s:
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            filters = [Example.style_id == style_id]
            if participation:
                filters.append(
                    Example.revision == Example.processed_revision
                    if participation == "used"
                    else Example.revision != Example.processed_revision
                )
            if role:
                filters.append(Example.last_result["role"].as_string() == role)
            if q:
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = "%" + escaped + "%"
                filters.append(
                    Example.user_input.ilike(pattern, escape="\\")
                    | Example.original_response.ilike(pattern, escape="\\")
                    | Example.desired_response.ilike(pattern, escape="\\")
                    | Example.correction_opinion.ilike(pattern, escape="\\")
                )
            total = await s.scalar(select(func.count()).select_from(Example).where(*filters))
            rows = await s.scalars(
                select(Example)
                .where(*filters)
                .order_by(Example.created_at.desc(), Example.id)
                .limit(limit)
                .offset(offset)
            )
            return {
                "items": [
                    {
                        **row_data(r),
                        "participation": "used" if r.revision == r.processed_revision else "unused",
                    }
                    for r in rows
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def append(self, style_id: str, value: ExampleInput, actor: str) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            if await s.get(Style, style_id) is None:
                raise LookupError("风格不存在")
            row = Example(style_id=style_id, **value.model_dump(), actor=actor)
            s.add(row)
            await s.flush()
            return row_data(row)

    async def edit(self, style_id: str, example_id: str, value: ExampleInput) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            row = await s.scalar(select(Example).where(Example.id == example_id).with_for_update())
            if row is None or row.style_id != style_id:
                raise LookupError("素材不存在")
            if any(getattr(row, k) != v for k, v in value.model_dump().items()):
                for key, val in value.model_dump().items():
                    setattr(row, key, val)
                row.revision += 1
                row.last_result = {}
            return row_data(row)

    async def delete(self, style_id: str, example_id: str) -> dict[str, str]:
        async with self.db.session() as s, s.begin():
            row = await s.get(Example, example_id)
            if row is None or row.style_id != style_id:
                raise LookupError("素材不存在")
            await s.delete(row)
            return {"id": example_id}
