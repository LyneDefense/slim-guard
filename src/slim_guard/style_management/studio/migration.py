"""Add a workbench without resetting any material, review, or published package."""

from typing import cast

from sqlalchemy import Table, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.expression_style.persona import PersonaContent, editable_content

from ..models import Example, Style, Version
from .legacy import merge_legacy_examples
from .models import StudioCheck, StudioDraft


async def migrate_style_studio(connection: AsyncConnection) -> None:
    for model in (StudioDraft, StudioCheck):
        await connection.run_sync(cast(Table, model.__table__).create, checkfirst=True)
    styles = (await connection.execute(select(Style.__table__))).mappings()
    for style in styles:
        if await connection.scalar(
            select(StudioDraft.style_id).where(StudioDraft.style_id == style["id"])
        ):
            continue
        package = await connection.scalar(
            select(Version.package).where(Version.id == style["active_version_id"])
        )
        try:
            content = editable_content(package) if package else PersonaContent()
        except ValueError:
            # The original package remains readable, with an explicit conversion
            # warning in the studio's active-version and history projections.
            content = PersonaContent()
        materials = list(
            (
                await connection.execute(
                    select(Example.__table__)
                    .where(Example.style_id == style["id"])
                    .order_by(Example.created_at, Example.id)
                )
            ).mappings()
        )
        content = merge_legacy_examples(content, [dict(row) for row in materials])
        await connection.execute(
            insert(StudioDraft).values(
                style_id=style["id"],
                revision=1,
                content=content.model_dump(mode="json"),
                content_hash=content.content_hash,
                source_version_id=style["active_version_id"],
                actor="system",
            )
        )
