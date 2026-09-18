"""Read-only published package adapter; no corpus retrieval or evaluation code."""

from sqlalchemy import select

from slim_guard.db.session import Database
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.package import StylePackage

from .models import Style, Version


class RuntimeStyles:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def resolve(self) -> str:
        async with self.database.session() as s:
            style = await s.scalar(select(Style).where(Style.is_default.is_(True)))
            return (style.active_version_id if style else None) or "doctor_builtin_v1"

    async def get_runtime_snapshot(self, version: str) -> StyleProfileSnapshot | None:
        async with self.database.session() as s:
            row = await s.get(Version, version)
            if row is None or row.status != "published":
                return None
            style = await s.get(Style, row.style_id)
            # The caller already selected this version. A concurrent activation must
            # not invalidate a Turn's frozen choice between resolve() and this read.
            if style is None:
                return None
            package = StylePackage.model_validate(row.package)
            if package.style_id != row.style_id or package.version_id != row.id:
                raise ValueError("风格产物归属错误")
            return package.runtime_snapshot()
