from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, insert, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from slim_guard.db.models import (
    Base,
    SchemaMigrationRecord,
    StyleProfileRecord,
    StyleProfileVersionRecord,
    StyleRuntimeConfigurationRecord,
)

_DEFAULT_STYLE_PROFILE_ID = "slimguard_default"
_DEFAULT_STYLE_PROFILE_VERSION = "slimguard_default_v1"
_DEFAULT_STYLE_PROFILE_VERSION_ID = "style-default-v1"
_DEFAULT_STYLE_PROFILE_PROMPT = (
    "Write clear, concise and supportive SlimGuard replies. Preserve every fact, "
    "record status, risk, uncertainty and citation. Never shame, frighten, impersonate "
    "a person, or add medical conclusions."
)
# Frozen with the migration: future profile edits must append a new version.
_DEFAULT_STYLE_SPEC = {
    "display_name": "SlimGuard 默认简洁语气",
    "description": "接近现有微信回复体验：自然、简洁、明确，不冒充真人或新增判断。",
    "tone_rules": [
        "使用自然简洁的中文微信语气",
        "先准确表达既定内容，再给必要的下一步",
        "普通确认保持短句，不堆叠标题或口号",
        "直接但不羞辱、不恐吓、不冒充医生或其他真人",
    ],
    "prohibited_phrases": [
        "作为章医生",
        "我是章医生",
        "保证瘦",
        "一定能瘦",
    ],
    "preferred_max_paragraphs": 3,
}
_LEGACY_STYLE_AB_SCENARIO = json.dumps(
    {
        "title": "历史 A/B 用例（未单独记录场景）",
        "user_situation": "该用例创建于结构化场景字段上线之前。",
        "known_context": [
            "请结合下方合成 ResponsePlan 和两侧输出查看；旧评分历史保持不变。"
        ],
        "response_goal": "比较同一份合成 ResponsePlan 在两个风格版本下的表达。",
    },
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
    sort_keys=True,
)
_LEGACY_STYLE_AB_SCENARIO_SHA256 = hashlib.sha256(
    _LEGACY_STYLE_AB_SCENARIO.encode()
).hexdigest()


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: str
    apply: Callable[[AsyncConnection], Awaitable[None]]


async def _create_application_tables(connection: AsyncConnection) -> None:
    """Additive migration safe for both an existing SQLite DB and a fresh DB."""

    await connection.run_sync(Base.metadata.create_all)


async def _add_memory_evidence_item(connection: AsyncConnection) -> None:
    """Persist the user-authored fact source separately from the current action."""

    await _create_application_tables(connection)
    columns = await connection.run_sync(
        lambda sync_connection: {
            column["name"] for column in inspect(sync_connection).get_columns("user_memory_facts")
        }
    )
    if "evidence_item_id" in columns:
        return
    await connection.execute(
        text("ALTER TABLE user_memory_facts ADD COLUMN evidence_item_id VARCHAR(36)")
    )
    await connection.execute(
        text(
            "UPDATE user_memory_facts SET evidence_item_id = source_item_id "
            "WHERE evidence_item_id IS NULL"
        )
    )


async def _allow_mobile_test_account_identity(connection: AsyncConnection) -> None:
    """Allow the development-only test account identity provider on PostgreSQL."""

    await _create_application_tables(connection)
    if connection.dialect.name != "postgresql":
        # Test databases are created from current metadata. SlimGuard production
        # deployments use PostgreSQL, where an existing constraint must be replaced.
        return
    await connection.execute(
        text(
            "ALTER TABLE mobile_auth_identities "
            "DROP CONSTRAINT IF EXISTS ck_mobile_identity_provider"
        )
    )
    await connection.execute(
        text(
            "ALTER TABLE mobile_auth_identities "
            "ADD CONSTRAINT ck_mobile_identity_provider "
            "CHECK (provider IN ('phone','apple','test_account'))"
        )
    )


async def _create_and_seed_style_profiles(connection: AsyncConnection) -> None:
    """Create the versioned profile tables and install the conservative default."""

    await _create_application_tables(connection)
    profile_id = await connection.scalar(
        select(StyleProfileRecord.id).where(
            StyleProfileRecord.stable_name == _DEFAULT_STYLE_PROFILE_ID
        )
    )
    if profile_id is None:
        profile_id = _DEFAULT_STYLE_PROFILE_ID
        await connection.execute(
            insert(StyleProfileRecord).values(
                id=profile_id,
                stable_name=_DEFAULT_STYLE_PROFILE_ID,
                active_version_id=None,
                is_default=True,
            )
        )
    version_id = await connection.scalar(
        select(StyleProfileVersionRecord.id).where(
            StyleProfileVersionRecord.version == _DEFAULT_STYLE_PROFILE_VERSION
        )
    )
    if version_id is None:
        version_id = _DEFAULT_STYLE_PROFILE_VERSION_ID
        await connection.execute(
            insert(StyleProfileVersionRecord).values(
                id=version_id,
                profile_id=profile_id,
                version=_DEFAULT_STYLE_PROFILE_VERSION,
                style_spec_json=json.dumps(
                    _DEFAULT_STYLE_SPEC,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                prompt_sha256=hashlib.sha256(_DEFAULT_STYLE_PROFILE_PROMPT.encode()).hexdigest(),
                source_corpus_sha256=None,
                status="active",
            )
        )
    await connection.execute(
        update(StyleProfileRecord)
        .where(
            StyleProfileRecord.id == profile_id,
            StyleProfileRecord.active_version_id.is_(None),
        )
        .values(active_version_id=version_id)
    )


async def _create_style_ab_review_tables(connection: AsyncConnection) -> None:
    """Create the online-safe A/B ledger and enforce immutable human scores."""

    await _create_application_tables(connection)
    if connection.dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS "
                    f"style_ab_human_reviews_{operation.lower()}_blocked "
                    f"BEFORE {operation} ON style_ab_human_reviews BEGIN "
                    "SELECT RAISE(ABORT, 'Style A/B reviews are append-only'); END"
                )
            )
    elif connection.dialect.name == "postgresql":
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION block_style_ab_review_mutation() "
                "RETURNS trigger AS $$ BEGIN "
                "RAISE EXCEPTION 'Style A/B reviews are append-only'; "
                "END; $$ LANGUAGE plpgsql"
            )
        )
        await connection.execute(
            text(
                "DROP TRIGGER IF EXISTS style_ab_human_reviews_mutation_blocked "
                "ON style_ab_human_reviews"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER style_ab_human_reviews_mutation_blocked "
                "BEFORE UPDATE OR DELETE ON style_ab_human_reviews "
                "FOR EACH ROW EXECUTE FUNCTION block_style_ab_review_mutation()"
            )
        )


async def _add_style_ab_scenarios(connection: AsyncConnection) -> None:
    """Bind human-readable context to new A/B cases and label legacy rows explicitly."""

    await _create_application_tables(connection)
    columns = await connection.run_sync(
        lambda sync_connection: {
            column["name"]
            for column in inspect(sync_connection).get_columns("style_ab_evaluation_cases")
        }
    )
    scenario_columns_added = (
        "scenario_json" not in columns or "scenario_sha256" not in columns
    )
    if "scenario_json" not in columns:
        await connection.execute(
            text("ALTER TABLE style_ab_evaluation_cases ADD COLUMN scenario_json TEXT")
        )
    if "scenario_sha256" not in columns:
        await connection.execute(
            text(
                "ALTER TABLE style_ab_evaluation_cases "
                "ADD COLUMN scenario_sha256 VARCHAR(64)"
            )
        )
    await connection.execute(
        text(
            "UPDATE style_ab_evaluation_cases "
            "SET scenario_json = :scenario, scenario_sha256 = :scenario_sha256 "
            "WHERE scenario_json IS NULL OR scenario_sha256 IS NULL"
        ),
        {
            "scenario": _LEGACY_STYLE_AB_SCENARIO,
            "scenario_sha256": _LEGACY_STYLE_AB_SCENARIO_SHA256,
        },
    )
    if connection.dialect.name == "postgresql" and scenario_columns_added:
        await connection.execute(
            text(
                "ALTER TABLE style_ab_evaluation_cases "
                "ALTER COLUMN scenario_json SET NOT NULL, "
                "ALTER COLUMN scenario_sha256 SET NOT NULL"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE style_ab_evaluation_cases "
                "ADD CONSTRAINT ck_style_ab_case_scenario_sha256 "
                "CHECK (length(scenario_sha256) = 64)"
            )
        )


async def _create_style_correction_feedback(connection: AsyncConnection) -> None:
    """Create the real-test correction ledger and make every entry immutable."""

    await _create_application_tables(connection)
    if connection.dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS "
                    f"style_correction_feedback_{operation.lower()}_blocked "
                    f"BEFORE {operation} ON style_correction_feedback BEGIN "
                    "SELECT RAISE(ABORT, 'Style correction feedback is append-only'); END"
                )
            )
    elif connection.dialect.name == "postgresql":
        await connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION block_style_correction_feedback_mutation() "
                "RETURNS trigger AS $$ BEGIN "
                "RAISE EXCEPTION 'Style correction feedback is append-only'; "
                "END; $$ LANGUAGE plpgsql"
            )
        )
        await connection.execute(
            text(
                "DROP TRIGGER IF EXISTS style_correction_feedback_mutation_blocked "
                "ON style_correction_feedback"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER style_correction_feedback_mutation_blocked "
                "BEFORE UPDATE OR DELETE ON style_correction_feedback "
                "FOR EACH ROW EXECUTE FUNCTION block_style_correction_feedback_mutation()"
            )
        )


async def _create_style_iteration_control_plane(connection: AsyncConnection) -> None:
    """Create durable iteration jobs, immutable inputs/events, and runtime routing."""

    await _create_application_tables(connection)
    current = await connection.scalar(
        select(StyleRuntimeConfigurationRecord.id).where(
            StyleRuntimeConfigurationRecord.id == "default"
        )
    )
    if current is None:
        await connection.execute(
            insert(StyleRuntimeConfigurationRecord).values(
                id="default",
                active_profile_version=_DEFAULT_STYLE_PROFILE_VERSION,
                previous_profile_version=None,
                revision=0,
                updated_by="system-bootstrap",
            )
        )
    immutable_tables = (
        "style_iteration_inputs",
        "style_iteration_events",
        "style_activation_events",
    )
    if connection.dialect.name == "sqlite":
        for table_name in immutable_tables:
            for operation in ("UPDATE", "DELETE"):
                await connection.execute(
                    text(
                        "CREATE TRIGGER IF NOT EXISTS "
                        f"{table_name}_{operation.lower()}_blocked "
                        f"BEFORE {operation} ON {table_name} BEGIN "
                        f"SELECT RAISE(ABORT, '{table_name} records are append-only'); END"
                    )
                )
    elif connection.dialect.name == "postgresql":
        for table_name in immutable_tables:
            function_name = f"block_{table_name}_mutation"
            trigger_name = f"{table_name}_mutation_blocked"
            await connection.execute(
                text(
                    f"CREATE OR REPLACE FUNCTION {function_name}() "
                    "RETURNS trigger AS $$ BEGIN "
                    f"RAISE EXCEPTION '{table_name} records are append-only'; "
                    "END; $$ LANGUAGE plpgsql"
                )
            )
            await connection.execute(
                text(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
            )
            await connection.execute(
                text(
                    f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OR DELETE ON {table_name} "
                    f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
                )
            )


MIGRATIONS = (
    SchemaMigration("20260831_01_interaction_tracing", _create_application_tables),
    SchemaMigration("20260902_01_body_fat_records", _create_application_tables),
    SchemaMigration("20260902_02_memory_evidence_refs", _add_memory_evidence_item),
    SchemaMigration("20260902_03_memory_index_outbox", _create_application_tables),
    SchemaMigration("20260903_01_mobile_accounts", _create_application_tables),
    SchemaMigration("20260903_02_mobile_devices_and_bindings", _create_application_tables),
    SchemaMigration(
        "20260903_03_mobile_test_accounts",
        _allow_mobile_test_account_identity,
    ),
    SchemaMigration("20260904_01_multi_agent_audit", _create_application_tables),
    SchemaMigration("20260905_01_style_profiles", _create_and_seed_style_profiles),
    SchemaMigration("20260906_01_nutrition_knowledge", _create_application_tables),
    SchemaMigration("20260908_01_style_ab_reviews", _create_style_ab_review_tables),
    SchemaMigration("20260908_02_style_ab_scenarios", _add_style_ab_scenarios),
    SchemaMigration(
        "20260908_03_style_correction_feedback",
        _create_style_correction_feedback,
    ),
    SchemaMigration(
        "20260909_01_style_iteration_control_plane",
        _create_style_iteration_control_plane,
    ),
)


async def migrate(connection: AsyncConnection) -> tuple[str, ...]:
    """Apply pending, application-owned schema migrations in version order."""

    # Bootstrap only the migration ledger before querying it. The first migration
    # then creates every application table that is missing from an existing DB.
    await connection.run_sync(
        lambda sync_connection: cast(Table, SchemaMigrationRecord.__table__).create(
            sync_connection,
            checkfirst=True,
        )
    )
    applied = set(await connection.scalars(select(SchemaMigrationRecord.version)))
    completed: list[str] = []
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        await migration.apply(connection)
        await connection.execute(insert(SchemaMigrationRecord).values(version=migration.version))
        completed.append(migration.version)
    return tuple(completed)
