"""Conservative diet guidance derived only from validated dish rules."""

from __future__ import annotations

import re

from slim_guard.agents.contracts import Confidence, InvocationStatus
from slim_guard.agents.diet_guidance.contracts import (
    DietGuidanceAction,
    DietGuidanceAgentResult,
    DietGuidanceAssessment,
    DietGuidanceReason,
    DietGuidanceScope,
    DishSuitability,
    DishSuitabilityAssessment,
)
from slim_guard.agents.nutrition_retrieval import (
    DishEvidence,
    DishEvidenceBundle,
    DishRuleEffect,
)

_EFFECT_PRIORITY = {
    DishRuleEffect.ALLOW: 1,
    DishRuleEffect.ADJUST: 2,
    DishRuleEffect.LIMIT: 3,
    DishRuleEffect.REQUIRE_CONFIRMATION: 4,
    DishRuleEffect.AVOID: 5,
}
_SUITABILITY = {
    DishRuleEffect.ALLOW: DishSuitability.SUITABLE,
    DishRuleEffect.ADJUST: DishSuitability.SUITABLE_WITH_ADJUSTMENT,
    DishRuleEffect.LIMIT: DishSuitability.LIMIT,
    DishRuleEffect.AVOID: DishSuitability.AVOID,
}
_FORBIDDEN_ESTIMATE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:千卡|卡路里|kcal|克|g\b)|"
    r"(?:热量|蛋白质|脂肪|碳水)[^。；，,]{0,12}\d)",
    re.IGNORECASE,
)


class DietGuidanceAgent:
    """This first usable version is deterministic and therefore fail-closed."""

    def run(self, evidence: DishEvidenceBundle) -> DietGuidanceAgentResult:
        try:
            assessments = tuple(self._assess(item) for item in evidence.dishes)
            questions = tuple(
                item.uncertainty_note
                for item in assessments
                if item.suitability is DishSuitability.INSUFFICIENT_INFORMATION
                and item.uncertainty_note is not None
            )[:8]
            scope = (
                DietGuidanceScope.CONSTRAINT_SPECIFIC
                if any(item.user_constraint_refs for item in assessments)
                else DietGuidanceScope.WEIGHT_MANAGEMENT_GENERAL
            )
            return DietGuidanceAgentResult(
                status=InvocationStatus.SUCCEEDED,
                assessment=DietGuidanceAssessment(
                    scope=scope,
                    dishes=assessments,
                    questions=questions,
                ),
            )
        except (TypeError, ValueError):
            return DietGuidanceAgentResult(
                status=InvocationStatus.FAILED,
                assessment=None,
                failure_code="diet_guidance_integrity_invalid",
            )

    def _assess(self, evidence: DishEvidence) -> DishSuitabilityAssessment:
        canonical_name = evidence.entity_match.canonical_name or evidence.entity_match.query_name
        safe_rules = tuple(
            item for item in evidence.rules if not _FORBIDDEN_ESTIMATE.search(item.statement)
        )
        if not safe_rules:
            note = "；".join(evidence.missing_information) or "没有足够的已审核规则"
            return DishSuitabilityAssessment(
                dish_ref=evidence.dish_ref,
                canonical_name=canonical_name,
                suitability=DishSuitability.INSUFFICIENT_INFORMATION,
                uncertainty_note=f"{canonical_name}：{note}，暂不判断能不能吃。",
            )
        strongest = max(safe_rules, key=lambda item: _EFFECT_PRIORITY[item.effect])
        if strongest.effect is DishRuleEffect.REQUIRE_CONFIRMATION:
            return DishSuitabilityAssessment(
                dish_ref=evidence.dish_ref,
                canonical_name=canonical_name,
                suitability=DishSuitability.INSUFFICIENT_INFORMATION,
                uncertainty_note=f"{canonical_name}还需要确认具体做法或配料。",
            )
        selected = tuple(item for item in safe_rules if item.effect is strongest.effect)
        reasons = tuple(
            DietGuidanceReason(
                reason_id=f"reason-{index + 1}-{evidence.dish_ref}",
                statement=item.statement,
                evidence_refs=(item.rule_id,),
                confidence=Confidence.HIGH,
            )
            for index, item in enumerate(selected)
        )
        actions = tuple(
            DietGuidanceAction(
                action_id=f"action-{index + 1}-{evidence.dish_ref}",
                statement=item.statement,
                basis_reason_ids=(reasons[index].reason_id,),
            )
            for index, item in enumerate(selected[:3])
        )
        user_refs = tuple(
            dict.fromkeys(
                reference
                for item in selected
                for reference in item.user_constraint_refs
            )
        )
        hard_refs = (
            tuple(item.rule_id for item in selected)
            if strongest.effect is DishRuleEffect.AVOID
            else ()
        )
        return DishSuitabilityAssessment(
            dish_ref=evidence.dish_ref,
            canonical_name=canonical_name,
            suitability=_SUITABILITY[strongest.effect],
            reasons=reasons,
            actions=actions,
            hard_rule_refs=hard_refs,
            user_constraint_refs=user_refs,
        )


__all__ = ["DietGuidanceAgent"]
