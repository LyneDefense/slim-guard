"""Explicit development reset, limited to the obsolete style subsystem."""

from typing import cast

from sqlalchemy import Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import BuildRun, Style, Version
from .trainer_migration import seed_builtin

DOCTOR_ID = "doctor"
DOCTOR_VERSION = "doctor_builtin_v1"
OBSOLETE_TABLES = (
    "style_example_reviews",
    "style_examples",
    "style_ab_human_reviews",
    "style_ab_evaluation_cases",
    "style_correction_feedback",
    "style_iteration_inputs",
    "style_iteration_events",
    "style_activation_events",
    "style_runtime_configuration",
    "style_iteration_runs",
    "style_profile_versions",
    "style_profiles",
)


async def reset_style_schema(connection: AsyncConnection) -> None:
    # User explicitly authorized discarding development style data. Do not touch other features.
    for table in OBSOLETE_TABLES:
        await connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
    if connection.dialect.name == "postgresql":
        for name in (
            "block_style_ab_review_mutation",
            "block_style_correction_feedback_mutation",
            "block_style_iteration_inputs_mutation",
            "block_style_iteration_events_mutation",
            "block_style_activation_events_mutation",
        ):
            await connection.execute(text(f'DROP FUNCTION IF EXISTS "{name}"()'))
    for model_table in (Style.__table__, BuildRun.__table__, Version.__table__):
        await connection.run_sync(cast(Table, model_table).create, checkfirst=True)
    if await connection.scalar(select(Style.id).where(Style.id == DOCTOR_ID)) is None:
        await connection.execute(
            insert(Style).values(
                id=DOCTOR_ID,
                name="医生风格",
                description="自然、简短、直接、克制；只转换表达。",
                is_default=True,
                active_version_id=DOCTOR_VERSION,
            )
        )
        await seed_builtin(connection)
