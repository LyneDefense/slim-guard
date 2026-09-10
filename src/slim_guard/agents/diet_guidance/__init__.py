"""Evidence-bound dish suitability assessment contracts."""

from slim_guard.agents.diet_guidance.agent import DietGuidanceAgent
from slim_guard.agents.diet_guidance.contracts import (
    DietGuidanceAction,
    DietGuidanceAgentResult,
    DietGuidanceAssessment,
    DietGuidanceReason,
    DietGuidanceScope,
    DishSuitability,
    DishSuitabilityAssessment,
)
from slim_guard.agents.diet_guidance.prompt import (
    DIET_GUIDANCE_PROMPT,
    DIET_GUIDANCE_PROMPT_VERSION,
)

__all__ = [
    "DietGuidanceAgent",
    "DIET_GUIDANCE_PROMPT",
    "DIET_GUIDANCE_PROMPT_VERSION",
    "DietGuidanceAction",
    "DietGuidanceAgentResult",
    "DietGuidanceAssessment",
    "DietGuidanceReason",
    "DietGuidanceScope",
    "DishSuitability",
    "DishSuitabilityAssessment",
]
