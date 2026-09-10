from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from slim_guard.db.session import Database
from slim_guard.dish_knowledge import (
    DishAliasDocument,
    DishCatalogConflict,
    DishCatalogDocument,
    DishCatalogGovernanceError,
    DishCatalogMatchStatus,
    DishCatalogRepository,
    DishCatalogService,
    DishRuleDocument,
    DishTraitDocument,
)


def _document(*, version: str = "2026-09-10") -> DishCatalogDocument:
    return DishCatalogDocument(
        entity_key="tomato_scrambled_egg",
        version=version,
        canonical_name="番茄炒蛋",
        cuisine="中式家常菜",
        source_refs=("catalog-source-1",),
        aliases=(
            DishAliasDocument(
                name="西红柿炒鸡蛋",
                region="中国大陆",
                source_ref="catalog-source-1",
            ),
        ),
        traits=(
            DishTraitDocument(
                trait_key="cooking.adjustable_oil",
                statement="烹调油和加糖量随做法变化，应在建议前询问。",
                certainty="possible",
                source_ref="catalog-source-1",
            ),
        ),
        rules=(
            DishRuleDocument(
                rule_key="goal.weight_management.adjust",
                condition_type="goal",
                condition_value="weight_management",
                effect="adjust",
                statement="减油并避免额外加糖。",
                applicability=("adult",),
                source_ref="guideline-source-1",
                version="1",
            ),
        ),
    )


async def _database(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'dish.sqlite3'}")
    await database.create_schema()
    return database


async def test_only_published_dishes_are_searchable_and_aliases_match(tmp_path) -> None:
    database = await _database(tmp_path)
    try:
        repository = DishCatalogRepository(database)
        service = DishCatalogService(repository)
        imported = await service.import_documents((_document(),), imported_by="admin")
        entity_id = imported.entries[0].entry.entity.id

        assert (await repository.search_published("番茄炒蛋")).status is (
            DishCatalogMatchStatus.NOT_FOUND
        )
        await service.approve(entity_id, reviewer="reviewer")
        await service.publish(entity_id, reviewer="publisher")

        canonical = await repository.search_published(" 番茄 炒蛋 ")
        alias = await repository.search_published("西红柿炒鸡蛋")
        assert canonical.status is DishCatalogMatchStatus.EXACT
        assert alias.status is DishCatalogMatchStatus.ALIAS
        assert alias.candidates[0].entity.id == entity_id
    finally:
        await database.close()


async def test_catalog_import_is_idempotent_and_version_content_is_immutable(tmp_path) -> None:
    database = await _database(tmp_path)
    try:
        service = DishCatalogService(DishCatalogRepository(database))
        first = await service.import_documents((_document(),), imported_by="admin")
        repeated = await service.import_documents((_document(),), imported_by="admin")
        assert first.entries[0].created is True
        assert repeated.entries[0].created is False
        assert repeated.batch.duplicate_count == 1

        changed = _document().model_copy(update={"canonical_name": "番茄鸡蛋"})
        with pytest.raises(DishCatalogConflict, match="different content"):
            await service.import_documents((changed,), imported_by="admin")
    finally:
        await database.close()


async def test_review_history_is_append_only_and_new_version_needs_old_retired(tmp_path) -> None:
    database = await _database(tmp_path)
    try:
        repository = DishCatalogRepository(database)
        service = DishCatalogService(repository)
        first = await service.import_documents((_document(),), imported_by="admin")
        second = await service.import_documents(
            (_document(version="2026-09-11"),), imported_by="admin"
        )
        first_id = first.entries[0].entry.entity.id
        second_id = second.entries[0].entry.entity.id
        await service.approve(first_id, reviewer="reviewer")
        await service.publish(first_id, reviewer="publisher")
        await service.approve(second_id, reviewer="reviewer")
        with pytest.raises(DishCatalogGovernanceError, match="currently published"):
            await service.publish(second_id, reviewer="publisher")

        reviews = await repository.list_reviews(first_id)
        assert [item.decision for item in reviews] == ["approve", "publish"]
        async with database.session() as session:
            with pytest.raises(Exception, match="append-only"):
                await session.execute(
                    text("UPDATE dish_catalog_reviews SET reviewer='x' WHERE id=:id"),
                    {"id": reviews[0].id},
                )
                await session.commit()
    finally:
        await database.close()


def test_unconditional_avoid_rules_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Avoid rules cannot be unconditional"):
        DishRuleDocument(
            rule_key="unsafe.avoid",
            condition_type="general",
            effect="avoid",
            statement="不要吃。",
            source_ref="unknown",
            version="1",
        )
