"""Dish identity and nutrition evidence retrieval contracts."""

from slim_guard.agents.nutrition_retrieval.contracts import (
    DishEntityMatch,
    DishEntityMatchStatus,
    DishEvidence,
    DishEvidenceBundle,
    DishLookupInput,
    DishLookupPlan,
    DishRuleEffect,
    DishRuleEvidence,
    DishTraitEvidence,
    NutritionRetrievalResult,
)
from slim_guard.agents.nutrition_retrieval.prompt import (
    NUTRITION_RETRIEVAL_PROMPT,
    NUTRITION_RETRIEVAL_PROMPT_VERSION,
)

__all__ = [
    "DishEntityMatch",
    "DishEntityMatchStatus",
    "DishEvidence",
    "DishEvidenceBundle",
    "DishLookupInput",
    "DishLookupPlan",
    "DishRuleEffect",
    "DishRuleEvidence",
    "DishTraitEvidence",
    "NUTRITION_RETRIEVAL_PROMPT",
    "NUTRITION_RETRIEVAL_PROMPT_VERSION",
    "NutritionRetrievalResult",
]
