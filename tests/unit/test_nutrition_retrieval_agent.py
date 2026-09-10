from __future__ import annotations

from slim_guard.agents.dish_recognition import (
    ConfirmedDish,
    ConfirmedDishSet,
    DishConfirmationSource,
)
from slim_guard.agents.nutrition_retrieval import (
    DishConstraintInput,
    DishLookupInput,
    DishLookupPlan,
    NutritionRetrievalAgent,
)
from slim_guard.db.session import Database
from slim_guard.dish_knowledge import (
    DishCatalogDocument,
    DishCatalogRepository,
    DishCatalogService,
    DishRuleDocument,
)


def _confirmed(name: str = "花生拌菠菜") -> ConfirmedDishSet:
    return ConfirmedDishSet(
        dishes=(
            ConfirmedDish(
                dish_ref="dish-1",
                name=name,
                source=DishConfirmationSource.USER_TEXT,
            ),
        )
    )


async def _published_catalog(tmp_path) -> tuple[Database, DishCatalogRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval.sqlite3'}")
    await database.create_schema()
    repository = DishCatalogRepository(database)
    service = DishCatalogService(repository)
    result = await service.import_documents(
        (
            DishCatalogDocument(
                entity_key="peanut_spinach",
                version="1",
                canonical_name="花生拌菠菜",
                source_refs=("dish-source",),
                rules=(
                    DishRuleDocument(
                        rule_key="weight.adjust",
                        condition_type="goal",
                        condition_value="weight_management",
                        effect="adjust",
                        statement="酱汁分开放，按需要少量加入。",
                        applicability=("adult",),
                        source_ref="guideline-source",
                        version="1",
                    ),
                    DishRuleDocument(
                        rule_key="peanut_allergy.avoid",
                        condition_type="constraint",
                        condition_value="peanut_allergy",
                        effect="avoid",
                        statement="已明确花生过敏时应避免这道含花生的菜。",
                        applicability=("adult",),
                        source_ref="allergy-source",
                        version="1",
                    ),
                ),
            ),
        ),
        imported_by="admin",
    )
    entity_id = result.entries[0].entry.entity.id
    await service.approve(entity_id, reviewer="reviewer")
    await service.publish(entity_id, reviewer="publisher")
    return database, repository


async def test_retrieval_uses_published_rules_and_only_matching_constraints(tmp_path) -> None:
    database, repository = await _published_catalog(tmp_path)
    try:
        agent = NutritionRetrievalAgent(catalog=repository)
        no_allergy = await agent.run(invocation_id="inv-1", dishes=_confirmed())
        assert no_allergy.evidence is not None
        assert [rule.effect.value for rule in no_allergy.evidence.dishes[0].rules] == [
            "adjust"
        ]

        plan = DishLookupPlan(
            dishes=(DishLookupInput(dish_ref="dish-1", name="花生拌菠菜"),),
            user_goal_tags=("weight_management",),
            applicability_tags=("adult",),
            constraints=(
                DishConstraintInput(
                    value="peanut_allergy",
                    evidence_ref="user-memory-allergy",
                ),
            ),
        )
        allergy = await agent.run(
            invocation_id="inv-2",
            dishes=_confirmed(),
            plan=plan,
        )
        assert allergy.evidence is not None
        avoid = next(
            item for item in allergy.evidence.dishes[0].rules if item.effect.value == "avoid"
        )
        assert avoid.user_constraint_refs == ("user-memory-allergy",)
    finally:
        await database.close()


async def test_unknown_dish_returns_explicit_missing_information(tmp_path) -> None:
    database, repository = await _published_catalog(tmp_path)
    try:
        result = await NutritionRetrievalAgent(catalog=repository).run(
            invocation_id="inv-1",
            dishes=_confirmed("未知地方菜"),
        )
        assert result.evidence is not None
        item = result.evidence.dishes[0]
        assert item.entity_match.status.value == "not_found"
        assert "菜品库中没有已发布条目" in item.missing_information
    finally:
        await database.close()
