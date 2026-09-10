from __future__ import annotations

import pytest
from pydantic import ValidationError

from slim_guard.agents.diet_guidance import (
    DietGuidanceAction,
    DietGuidanceAssessment,
    DietGuidanceReason,
    DishSuitabilityAssessment,
)
from slim_guard.agents.dish_recognition import (
    ConfirmedDish,
    ConfirmedDishSet,
    DishCandidate,
    DishRecognitionResult,
    RecognizedDish,
)
from slim_guard.agents.nutrition_retrieval import (
    DishEntityMatch,
    DishEvidence,
    DishEvidenceBundle,
    DishRuleEvidence,
)


def test_uncertain_recognition_requires_one_question() -> None:
    dish = RecognizedDish(
        dish_ref="dish-1",
        candidates=(
            DishCandidate(label="红烧茄子", confidence=0.62),
            DishCandidate(label="地三鲜", confidence=0.31),
        ),
        uncertainty_reasons=("土豆和青椒被遮挡",),
        requires_confirmation=True,
    )

    with pytest.raises(ValidationError, match="needs one question"):
        DishRecognitionResult(
            asset_id="asset-1",
            model="vision-test",
            prompt_version="dish-v1",
            policy_version="policy-v1",
            image_kind="meal",
            dishes=(dish,),
            overall_requires_confirmation=True,
        )

    result = DishRecognitionResult(
        asset_id="asset-1",
        model="vision-test",
        prompt_version="dish-v1",
        policy_version="policy-v1",
        image_kind="meal",
        dishes=(dish,),
        suggested_question="这道是红烧茄子还是地三鲜？",
        overall_requires_confirmation=True,
    )
    assert result.dishes[0].candidates[0].label == "红烧茄子"


def test_user_confirmation_requires_current_user_evidence() -> None:
    with pytest.raises(ValidationError, match="user evidence"):
        ConfirmedDish(
            dish_ref="dish-1",
            name="地三鲜",
            source="user_confirmed",
        )

    confirmed = ConfirmedDishSet(
        source_artifact_id="artifact-recognition-1",
        dishes=(
            ConfirmedDish(
                dish_ref="dish-1",
                name="地三鲜",
                source="user_confirmed",
                user_evidence_ref="item-user-confirmation-1",
            ),
        ),
    )
    assert confirmed.dishes[0].name == "地三鲜"


def test_avoid_rule_and_guidance_need_hard_applicability() -> None:
    with pytest.raises(ValidationError, match="Avoid rules require"):
        DishRuleEvidence(
            rule_id="rule-1",
            condition_type="goal",
            effect="avoid",
            statement="避免食用",
            source_refs=("source-1",),
        )

    with pytest.raises(ValidationError, match="Avoid guidance requires"):
        DishSuitabilityAssessment(
            dish_ref="dish-1",
            canonical_name="花生拌菠菜",
            suitability="avoid",
            reasons=(
                DietGuidanceReason(
                    reason_id="reason-1",
                    statement="含花生",
                    evidence_refs=("trait-peanut",),
                    confidence="high",
                ),
            ),
        )


def test_guidance_actions_and_bundle_keep_typed_references() -> None:
    bundle = DishEvidenceBundle(
        corpus_status="empty",
        dishes=(
            DishEvidence(
                dish_ref="dish-1",
                entity_match=DishEntityMatch(
                    status="not_found",
                    query_name="家常小炒",
                ),
                missing_information=("菜品库暂无匹配",),
            ),
        ),
    )
    assert bundle.corpus_status == "empty"

    assessment = DietGuidanceAssessment(
        scope="weight_management_general",
        dishes=(
            DishSuitabilityAssessment(
                dish_ref="dish-1",
                canonical_name="家常小炒",
                suitability="suitable_with_adjustment",
                reasons=(
                    DietGuidanceReason(
                        reason_id="reason-1",
                        statement="可通过烹饪方式调整",
                        evidence_refs=("trait-cooking-adjustment",),
                        confidence="medium",
                    ),
                ),
                actions=(
                    DietGuidanceAction(
                        action_id="action-1",
                        statement="优先选择少油做法",
                        basis_reason_ids=("reason-1",),
                    ),
                ),
            ),
        ),
    )
    assert assessment.dishes[0].actions[0].basis_reason_ids == ("reason-1",)
