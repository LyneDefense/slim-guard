"""Optimistic drafts, bounded release checks, and atomic publication."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import new_uuid, utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.persona import (
    PersonaContent,
    editable_content,
    make_persona_package,
)

from ..models import Example, Style, Version
from ..views import row_data
from .contracts import CheckInput, DraftInput, PublishInput, RestoreInput
from .errors import DraftConflictError
from .legacy import legacy_example
from .models import StudioCheck, StudioDraft


def version_view(row: Version, active_version_id: str | None) -> dict[str, Any]:
    reason = None
    try:
        content = editable_content(row.package).model_dump(mode="json")
    except ValueError as exc:
        content = None
        reason = str(exc)
    return {
        **row_data(row),
        "content": content,
        "legacy": row.package.get("schema_version") != "persona-1",
        "active": row.id == active_version_id,
        "diagnostics": row.report,
        "restore_block_reason": reason,
    }


def check_view(row: StudioCheck) -> dict[str, Any]:
    return {
        **row_data(row),
        **row.result,
        "release_eligible": row.result.get("passed") is True,
    }


class StudioRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    async def _style(s: AsyncSession, style_id: str) -> Style:
        style = await s.get(Style, style_id)
        if style is None:
            raise LookupError("风格不存在")
        return style

    async def ensure_style(self, style_id: str) -> None:
        async with self.db.session() as s:
            await self._style(s, style_id)

    async def _draft(self, s: AsyncSession, style: Style) -> StudioDraft:
        row = await s.get(StudioDraft, style.id)
        if row is not None:
            return row
        content = PersonaContent(expression=style.description[:1000])
        version = await s.get(Version, style.active_version_id) if style.active_version_id else None
        if version:
            try:
                content = editable_content(version.package)
            except ValueError:
                # Incompatible legacy configurations stay fully readable in history.
                # Do not silently seed a partially converted active persona.
                content = PersonaContent()
        row = StudioDraft(
            style_id=style.id,
            revision=1,
            content=content.model_dump(mode="json"),
            content_hash=content.content_hash,
            source_version_id=version.id if version else None,
            actor="system",
        )
        s.add(row)
        await s.flush()
        return row

    async def studio(self, style_id: str) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            style = await self._style(s, style_id)
            draft = await self._draft(s, style)
            active = (
                await s.get(Version, style.active_version_id) if style.active_version_id else None
            )
            legacy_rows = list(await s.scalars(select(Example).where(Example.style_id == style_id)))
            example_ids = {item["id"] for item in draft.content.get("examples", [])}
            return {
                "style": row_data(style),
                "draft": row_data(draft),
                "active_version": version_view(active, style.active_version_id) if active else None,
                "legacy_example_count": len(legacy_rows),
                "legacy_unimported_count": sum(row.id not in example_ids for row in legacy_rows),
            }

    @staticmethod
    async def _expect(s: AsyncSession, style_id: str, value: CheckInput) -> StudioDraft:
        # A conditional write also locks correctly on SQLite, where FOR UPDATE is
        # ignored. Saves cannot slip between this check and atomic publication.
        result = await s.execute(
            update(StudioDraft)
            .where(
                StudioDraft.style_id == style_id,
                StudioDraft.revision == value.revision,
                StudioDraft.content_hash == value.content_hash,
            )
            .values(revision=StudioDraft.revision)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise DraftConflictError("草稿已更新，请载入最新草稿后重试")
        row = await s.get(StudioDraft, style_id, populate_existing=True)
        assert row is not None
        return row

    async def save(
        self,
        style_id: str,
        value: DraftInput,
        actor: str,
        *,
        source_version_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            style = await self._style(s, style_id)
            await self._draft(s, style)
            values: dict[str, Any] = {
                "revision": StudioDraft.revision + 1,
                "content": value.content.model_dump(mode="json"),
                "content_hash": value.content.content_hash,
                "actor": actor,
                "updated_at": utc_now(),
            }
            if source_version_id is not None:
                values["source_version_id"] = source_version_id
            result = await s.execute(
                update(StudioDraft)
                .where(StudioDraft.style_id == style_id, StudioDraft.revision == value.revision)
                .values(**values)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise DraftConflictError("草稿已更新，请载入最新草稿后重试")
            row = await s.get(StudioDraft, style_id, populate_existing=True)
            assert row is not None
            return row_data(row)

    async def checked_content(self, style_id: str, value: CheckInput) -> PersonaContent:
        async with self.db.session() as s, s.begin():
            await self._style(s, style_id)
            row = await self._expect(s, style_id, value)
            return PersonaContent.model_validate(row.content)

    async def record_check(
        self,
        style_id: str,
        value: CheckInput,
        result: dict[str, Any],
        signature: str,
        actor: str,
    ) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            await self._expect(s, style_id, value)
            row = StudioCheck(
                style_id=style_id,
                revision=value.revision,
                content_hash=value.content_hash,
                signature=signature,
                result={
                    "passed": result.get("passed") is True,
                    "checks": result.get("checks", []),
                    "warnings": result.get("warnings", []),
                },
                actor=actor,
            )
            s.add(row)
            await s.flush()
            return check_view(row)

    async def publish(
        self, style_id: str, value: PublishInput, signature: str, actor: str
    ) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            style = await self._style(s, style_id)
            check = await s.get(StudioCheck, value.check_id)
            if check is None or check.style_id != style_id:
                raise LookupError("检查记录不存在")
            if check.revision != value.revision or check.content_hash != value.content_hash:
                raise ValueError("检查记录与待发布草稿不一致，请重新检查")
            if check.published_version_id:
                return await self._already_published(s, style, check)
            draft = await self._expect(s, style_id, value)
            # Another identical request may have committed while this request
            # waited for the draft lock. Refresh the receipt after taking it.
            await s.refresh(check)
            if check.published_version_id:
                return await self._already_published(s, style, check)
            if check.result.get("passed") is not True or check.signature != signature:
                raise ValueError("必要检查未通过或检查策略已更新，请重新检查")
            version_id = new_uuid()
            package = make_persona_package(
                style_id, version_id, style.name, PersonaContent.model_validate(draft.content)
            )
            package.runtime_snapshot()
            reservation = await s.execute(
                update(StudioCheck)
                .where(StudioCheck.id == check.id, StudioCheck.published_version_id.is_(None))
                .values(published_version_id=version_id)
            )
            if reservation.rowcount != 1:  # type: ignore[attr-defined]
                raise ValueError("此检查已发布，请刷新查看发布版本")
            row = Version(
                id=version_id,
                style_id=style_id,
                name=f"{style.name[:80]} · {utc_now():%Y%m%d-%H%M%S} · {version_id[:8]}",
                status="published",
                package=package.model_dump(mode="json"),
                report={
                    "workflow": "role-studio-v1",
                    "check_id": check.id,
                    "check_signature": signature,
                    "content_hash": value.content_hash,
                    "package_hash": package.package_hash,
                    **check.result,
                },
                actor=actor,
            )
            s.add(row)
            style.active_version_id = row.id
            draft.source_version_id = row.id
            await s.flush()
            return {
                "version": version_view(row, row.id),
                "active_version": version_view(row, row.id),
                "draft": row_data(draft),
                "already_published": False,
            }

    @staticmethod
    async def _already_published(
        s: AsyncSession, style: Style, check: StudioCheck
    ) -> dict[str, Any]:
        version = await s.get(Version, check.published_version_id)
        draft = await s.get(StudioDraft, style.id, populate_existing=True)
        await s.refresh(style)
        active = await s.get(Version, style.active_version_id) if style.active_version_id else None
        assert version is not None and draft is not None
        return {
            "version": version_view(version, style.active_version_id),
            "active_version": version_view(active, style.active_version_id) if active else None,
            "draft": row_data(draft),
            "already_published": True,
        }

    async def versions(self, style_id: str) -> dict[str, Any]:
        async with self.db.session() as s:
            style = await self._style(s, style_id)
            rows = await s.scalars(
                select(Version)
                .where(Version.style_id == style_id)
                .order_by(Version.created_at.desc(), Version.id)
            )
            return {"items": [version_view(row, style.active_version_id) for row in rows]}

    async def legacy_examples(self, style_id: str) -> dict[str, Any]:
        async with self.db.session() as s, s.begin():
            style = await self._style(s, style_id)
            draft = await self._draft(s, style)
            example_ids = {item["id"] for item in draft.content.get("examples", [])}
            rows = await s.scalars(
                select(Example)
                .where(Example.style_id == style_id)
                .order_by(Example.created_at, Example.id)
            )
            items = []
            for row in rows:
                data = row_data(row)
                _, reason = legacy_example(data)
                imported = row.id in example_ids
                if not imported and reason is None:
                    reason = (
                        "草稿最多容纳 64 组示例，请精简后手动添加"
                        if len(example_ids) >= 64
                        else "此素材不在当前草稿中，可复制为对话示例"
                    )
                items.append({**data, "imported": imported, "import_block_reason": reason})
            return {"items": items}

    async def restore(self, style_id: str, value: RestoreInput, actor: str) -> dict[str, Any]:
        async with self.db.session() as s:
            version = await s.get(Version, value.version_id)
            if version is None or version.style_id != style_id:
                raise LookupError("历史版本不存在")
            if version.status != "published":
                raise ValueError("只能将已发布版本加载为草稿")
            content = editable_content(version.package)
        return await self.save(
            style_id,
            DraftInput(revision=value.revision, content=content),
            actor,
            source_version_id=value.version_id,
        )
