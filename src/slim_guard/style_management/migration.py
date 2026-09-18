"""Explicit development reset, limited to the obsolete style subsystem."""

from typing import cast

from sqlalchemy import Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Style, Version

DOCTOR_ID = "doctor"
DOCTOR_VERSION = "doctor_builtin_v1"
DOCTOR_GUIDE = {
    "summary": "自然、简短、直接、克制的医生式表达；不自称真人医生。",
    "rules": [
        {
            "text": "忠实保留原意，用自然简洁的中文表达，不添加建议或口号。",
            "confidence": "stable",
            "evidence_ids": [],
        },
        {
            "text": "语气直接友善，不说教、不羞辱；原文简短时保持简短。",
            "confidence": "stable",
            "evidence_ids": [],
        },
        {
            "text": "原文的事实、数字、记录状态、风险和不确定性保持不变。",
            "confidence": "stable",
            "evidence_ids": [],
        },
    ],
    "prohibited_phrases": ["我是章医生", "作为章医生", "保证瘦", "一定能瘦"],
}

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
    for model_table in (Style.__table__, Version.__table__):
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
        await connection.execute(
            insert(Version).values(
                id=DOCTOR_VERSION,
                style_id=DOCTOR_ID,
                name=DOCTOR_VERSION,
                status="published",
                stage="系统初始版本",
                guide=DOCTOR_GUIDE,
                actor="system",
                snapshot={"examples": [], "feedback": [], "base_guide": {}},
                examples=[],
                events=[
                    {
                        "stage": "系统初始版本",
                        "message": "内置基础语气指引，无训练样例；可追加素材后构建专属版本",
                    }
                ],
            )
        )
