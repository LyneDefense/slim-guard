"""Development cutover: keep human material, remove obsolete builds and reviews."""

from datetime import datetime
from typing import cast

from sqlalchemy import Table, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import utc_now
from slim_guard.expression_style.package import Guide, make_package

from .models import BuildRun, Example, ReviewCase, Style, Version


def builtin_package() -> dict[str, object]:
    return make_package(
        "doctor",
        "doctor_builtin_v1",
        "医生风格",
        Guide(summary="系统基础表达：忠实原意，自然简短；不冒充医生本人。"),
    ).model_dump(mode="json")


async def seed_builtin(connection: AsyncConnection) -> None:
    if await connection.scalar(select(Version.id).where(Version.id == "doctor_builtin_v1")):
        return
    await connection.execute(
        insert(Version).values(
            id="doctor_builtin_v1",
            style_id="doctor",
            name="doctor_builtin_v1",
            status="published",
            package=builtin_package(),
            actor="system",
            report={},
        )
    )


async def migrate_trainer_schema(connection: AsyncConnection) -> None:
    # Authorized development cleanup is limited to expression-style derived data.
    # Read via SQL so both the previous and current metadata schemas are supported.
    originals = list(
        (await connection.execute(text("SELECT * FROM expression_examples"))).mappings()
    )
    await connection.execute(update(Style).values(active_version_id=None))
    for model in (ReviewCase, Version, BuildRun, Example):
        await connection.run_sync(cast(Table, model.__table__).drop, checkfirst=True)
    for model in (Example, BuildRun, Version, ReviewCase):
        await connection.run_sync(cast(Table, model.__table__).create, checkfirst=True)
    for row in originals:
        # Model-generated accepted outputs are not human style evidence.
        if row.get("source") == "accepted_review":
            continue
        await connection.execute(
            insert(Example).values(
                id=row["id"],
                style_id=row["style_id"],
                user_input=row["user_input"],
                original_response=row["original_response"],
                desired_response=row["desired_response"] or "",
                correction_opinion=row.get("correction_opinion", ""),
                revision=row.get("revision", 1),
                processed_revision=0,
                source=row.get("source", "manual"),
                actor=row["actor"],
                created_at=(
                    datetime.fromisoformat(row["created_at"])
                    if isinstance(row.get("created_at"), str)
                    else row.get("created_at") or utc_now()
                ),
            )
        )
    await seed_builtin(connection)
    await connection.execute(
        update(Style).where(Style.id == "doctor").values(active_version_id="doctor_builtin_v1")
    )
