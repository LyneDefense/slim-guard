from __future__ import annotations

from slim_guard.agents.diet_guidance import DietGuidanceAgent, DishSuitability
from slim_guard.agents.nutrition_retrieval import (
    DishEntityMatch,
    DishEvidence,
    DishEvidenceBundle,
    DishRuleEvidence,
)


def _bundle(*rules: DishRuleEvidence) -> DishEvidenceBundle:
    return DishEvidenceBundle(
        dishes=(
            DishEvidence(
                dish_ref="dish-1",
                entity_match=DishEntityMatch(
                    status="exact",
                    query_name="花生拌菠菜",
                    dish_entity_id="entity-1",
                    canonical_name="花生拌菠菜",
                    source_version="1",
                ),
                rules=rules,
                missing_information=("营养资料库尚未配置",),
            ),
        ),
        corpus_status="available",
    )


def test_guidance_uses_adjust_rule_without_inventing_nutrition_numbers() -> None:
    result = DietGuidanceAgent().run(
        _bundle(
            DishRuleEvidence(
                rule_id="rule-adjust",
                condition_type="goal",
                effect="adjust",
                statement="酱汁分开放，按需要少量加入。",
                applicability=("adult",),
                source_refs=("source-1",),
            )
        )
    )
    assert result.assessment is not None
    dish = result.assessment.dishes[0]
    assert dish.suitability is DishSuitability.SUITABLE_WITH_ADJUSTMENT
    assert dish.actions[0].statement == "酱汁分开放，按需要少量加入。"


def test_avoid_requires_and_preserves_matching_user_constraint() -> None:
    result = DietGuidanceAgent().run(
        _bundle(
            DishRuleEvidence(
                rule_id="rule-avoid",
                condition_type="constraint",
                effect="avoid",
                statement="已明确花生过敏时应避免这道含花生的菜。",
                applicability=("adult",),
                source_refs=("source-1",),
                user_constraint_refs=("memory-allergy",),
            )
        )
    )
    assert result.assessment is not None
    dish = result.assessment.dishes[0]
    assert dish.suitability is DishSuitability.AVOID
    assert dish.hard_rule_refs == ("rule-avoid",)
    assert dish.user_constraint_refs == ("memory-allergy",)


def test_missing_or_numeric_rules_degrade_to_insufficient_information() -> None:
    numeric = DishRuleEvidence(
        rule_id="rule-numeric",
        condition_type="goal",
        effect="adjust",
        statement="估算这一份是 500 千卡。",
        source_refs=("source-1",),
    )
    result = DietGuidanceAgent().run(_bundle(numeric))
    assert result.assessment is not None
    dish = result.assessment.dishes[0]
    assert dish.suitability is DishSuitability.INSUFFICIENT_INFORMATION
    assert dish.uncertainty_note is not None
