from __future__ import annotations

import json

from sqlalchemy import delete, insert, inspect, select, text

from slim_guard.db.migrations import MIGRATIONS
from slim_guard.db.models import (
    NutritionKnowledgeReviewEventRecord,
    NutritionKnowledgeSourceRecord,
    NutritionRetrievalProfileRecord,
    SchemaMigrationRecord,
)
from slim_guard.db.session import Database
from slim_guard.nutrition_knowledge import (
    KnowledgeDocument,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)
from slim_guard.nutrition_rag.profiles import (
    ANSWERABILITY_V1_QUERY_PLAN_VERSION,
    ANSWERABILITY_V1_RETRIEVAL_PROFILE_ID,
    DEFAULT_RETRIEVAL_PROFILE_ID,
    LEGACY_QUERY_PLAN_VERSION,
    LEGACY_RETRIEVAL_PROFILE_ID,
    QUERY_PLAN_VERSION,
)


async def test_existing_database_receives_body_fat_table_additively(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SchemaMigrationRecord.__table__.create(
                    sync_connection,
                    checkfirst=True,
                )
            )
            await connection.execute(
                insert(SchemaMigrationRecord).values(version="20260831_01_interaction_tracing")
            )

        completed = await database.migrate()
        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            evaluation_indexes = await connection.run_sync(
                lambda sync_connection: {
                    index["name"]
                    for index in inspect(sync_connection).get_indexes(
                        "nutrition_evaluation_runs"
                    )
                }
            )
            retrieval_profiles = {
                profile_id: query_plan_version
                for profile_id, query_plan_version in (
                    await connection.execute(
                        select(
                            NutritionRetrievalProfileRecord.id,
                            NutritionRetrievalProfileRecord.query_plan_version,
                        )
                    )
                ).tuples()
            }

        assert completed == (
            "20260902_01_body_fat_records",
            "20260902_02_memory_evidence_refs",
            "20260902_03_memory_index_outbox",
            "20260903_01_mobile_accounts",
            "20260903_02_mobile_devices_and_bindings",
            "20260903_03_mobile_test_accounts",
            "20260904_01_multi_agent_audit",
            "20260905_01_style_profiles",
            "20260906_01_nutrition_knowledge",
            "20260908_01_style_ab_reviews",
            "20260908_02_style_ab_scenarios",
            "20260908_03_style_correction_feedback",
            "20260909_01_style_iteration_control_plane",
            "20260910_01_dish_knowledge",
            "20260911_01_nutrition_hybrid_rag",
            "20260911_02_nutrition_release_governance",
            "20260912_01_mobile_coach_profiles",
            "20260916_01_nutrition_answerability_profile",
            "20260916_02_nutrition_answerability_v2_profile",
            "20260917_01_conversational_long_term_memory",
        )
        assert "body_fat_records" in table_names
        assert {
            "nutrition_knowledge_import_batches",
            "nutrition_knowledge_sources",
            "nutrition_knowledge_chunks",
            "nutrition_knowledge_reviews",
            "style_ab_evaluation_cases",
            "style_ab_human_reviews",
            "style_correction_feedback",
            "style_iteration_runs",
            "style_iteration_inputs",
            "style_iteration_events",
            "style_runtime_configuration",
            "style_activation_events",
            "dish_catalog_import_batches",
            "dish_entities",
            "dish_aliases",
            "dish_traits",
            "dish_rules",
            "dish_catalog_reviews",
            "nutrition_knowledge_assets",
            "nutrition_knowledge_source_labels",
            "nutrition_knowledge_jobs",
            "nutrition_knowledge_job_events",
            "nutrition_knowledge_sections",
            "nutrition_rag_chunks",
            "nutrition_chunk_embeddings",
            "nutrition_corpus_releases",
            "nutrition_corpus_runtime",
            "nutrition_retrieval_runs",
            "nutrition_evaluation_datasets",
            "mobile_coach_profiles",
        }.issubset(table_names)
        assert retrieval_profiles == {
            LEGACY_RETRIEVAL_PROFILE_ID: LEGACY_QUERY_PLAN_VERSION,
            ANSWERABILITY_V1_RETRIEVAL_PROFILE_ID: ANSWERABILITY_V1_QUERY_PLAN_VERSION,
            DEFAULT_RETRIEVAL_PROFILE_ID: QUERY_PLAN_VERSION,
        }
        assert "uq_nutrition_eval_open_release" in evaluation_indexes
    finally:
        await database.close()


async def test_legacy_published_nutrition_source_enters_release_governance(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'nutrition-governance.sqlite3'}")
    try:
        await database.create_schema()
        legacy = NutritionKnowledgeService(NutritionKnowledgeRepository(database))
        imported = await legacy.import_documents(
            (
                KnowledgeDocument(
                    source_key="legacy-guide",
                    version="2024",
                    title="历史营养指南",
                    publisher="测试机构",
                    content="成年人应保持食物多样，合理搭配。",
                ),
            ),
            imported_by="legacy-importer",
        )
        source_id = imported.documents[0].source.id
        await legacy.approve_source(source_id, reviewer="legacy-reviewer")
        await legacy.publish_source(source_id, reviewer="legacy-publisher")
        async with database.engine.begin() as connection:
            await connection.execute(
                delete(SchemaMigrationRecord).where(
                    SchemaMigrationRecord.version == "20260911_02_nutrition_release_governance"
                )
            )

        assert await database.migrate() == ("20260911_02_nutrition_release_governance",)
        async with database.engine.connect() as connection:
            source_status = await connection.scalar(
                select(NutritionKnowledgeSourceRecord.status).where(
                    NutritionKnowledgeSourceRecord.id == source_id
                )
            )
            review = (
                await connection.execute(
                    select(
                        NutritionKnowledgeReviewEventRecord.review_type,
                        NutritionKnowledgeReviewEventRecord.decision,
                        NutritionKnowledgeReviewEventRecord.actor,
                    ).where(NutritionKnowledgeReviewEventRecord.subject_id == source_id)
                )
            ).one()

        assert source_status == "draft"
        assert review == ("content", "approve", "legacy-publisher")
    finally:
        await database.close()


async def test_existing_memory_rows_backfill_their_original_evidence_item(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory-upgrade.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SchemaMigrationRecord.__table__.create(
                    sync_connection,
                    checkfirst=True,
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE user_memory_facts ("
                    "id VARCHAR(36) PRIMARY KEY, source_item_id VARCHAR(36) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO user_memory_facts (id, source_item_id) "
                    "VALUES ('memory-1', 'item-1')"
                )
            )
            for version in (
                "20260831_01_interaction_tracing",
                "20260902_01_body_fat_records",
            ):
                await connection.execute(insert(SchemaMigrationRecord).values(version=version))

        completed = await database.migrate()
        async with database.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("user_memory_facts")
                }
            )
            evidence_item_id = await connection.scalar(
                text("SELECT evidence_item_id FROM user_memory_facts WHERE id = 'memory-1'")
            )

        assert completed == (
            "20260902_02_memory_evidence_refs",
            "20260902_03_memory_index_outbox",
            "20260903_01_mobile_accounts",
            "20260903_02_mobile_devices_and_bindings",
            "20260903_03_mobile_test_accounts",
            "20260904_01_multi_agent_audit",
            "20260905_01_style_profiles",
            "20260906_01_nutrition_knowledge",
            "20260908_01_style_ab_reviews",
            "20260908_02_style_ab_scenarios",
            "20260908_03_style_correction_feedback",
            "20260909_01_style_iteration_control_plane",
            "20260910_01_dish_knowledge",
            "20260911_01_nutrition_hybrid_rag",
            "20260911_02_nutrition_release_governance",
            "20260912_01_mobile_coach_profiles",
            "20260916_01_nutrition_answerability_profile",
            "20260916_02_nutrition_answerability_v2_profile",
            "20260917_01_conversational_long_term_memory",
        )
        assert "evidence_item_id" in columns
        assert evidence_item_id == "item-1"
    finally:
        await database.close()


async def test_existing_style_ab_rows_receive_an_explicit_legacy_scenario(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'style-ab-upgrade.sqlite3'}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SchemaMigrationRecord.__table__.create(
                    sync_connection,
                    checkfirst=True,
                )
            )
            await connection.execute(
                text("CREATE TABLE style_ab_evaluation_cases (id VARCHAR(36) PRIMARY KEY)")
            )
            await connection.execute(
                text("INSERT INTO style_ab_evaluation_cases (id) VALUES ('TEST-case')")
            )
            for migration in MIGRATIONS:
                if migration.version == "20260908_02_style_ab_scenarios":
                    continue
                await connection.execute(
                    insert(SchemaMigrationRecord).values(version=migration.version)
                )

        assert await database.migrate() == ("20260908_02_style_ab_scenarios",)
        async with database.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("style_ab_evaluation_cases")
                }
            )
            row = (
                await connection.execute(
                    text(
                        "SELECT scenario_json, scenario_sha256 "
                        "FROM style_ab_evaluation_cases WHERE id = 'TEST-case'"
                    )
                )
            ).one()

        assert {"scenario_json", "scenario_sha256"}.issubset(columns)
        assert json.loads(row.scenario_json)["title"].startswith("历史 A/B 用例")
        assert len(row.scenario_sha256) == 64
    finally:
        await database.close()
