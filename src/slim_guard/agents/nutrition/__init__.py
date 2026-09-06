"""Public nutrition specialist API."""

from slim_guard.agents.nutrition.agent import (
    CONSERVATIVE_ASSESSMENT_TEXT,
    DEFAULT_NUTRITION_PROMPT_VERSION,
    NUTRITION_AGENT_ALLOWED_TOOLS,
    NUTRITION_AGENT_PROMPT,
    ConservativeNutritionFallback,
    NutritionAgent,
    NutritionAssessmentValidator,
)
from slim_guard.agents.nutrition.context import NutritionContextCompiler
from slim_guard.agents.nutrition.contracts import (
    CalculationInput,
    CalculationObservation,
    EvidenceAuthority,
    EvidencePacketInput,
    EvidencePacketLike,
    KnowledgeCorpusStatus,
    KnowledgeInput,
    KnowledgeRetrieval,
    NutritionAgentResult,
    NutritionContext,
    NutritionEvidence,
    NutritionValidationIssue,
    NutritionValidationIssueCode,
    NutritionValidationReport,
)

__all__ = [
    "CONSERVATIVE_ASSESSMENT_TEXT",
    "CalculationInput",
    "CalculationObservation",
    "ConservativeNutritionFallback",
    "EvidenceAuthority",
    "EvidencePacketInput",
    "EvidencePacketLike",
    "DEFAULT_NUTRITION_PROMPT_VERSION",
    "KnowledgeCorpusStatus",
    "KnowledgeInput",
    "KnowledgeRetrieval",
    "NutritionAgent",
    "NutritionAgentResult",
    "NUTRITION_AGENT_ALLOWED_TOOLS",
    "NUTRITION_AGENT_PROMPT",
    "NutritionAssessmentValidator",
    "NutritionContext",
    "NutritionContextCompiler",
    "NutritionEvidence",
    "NutritionValidationIssue",
    "NutritionValidationIssueCode",
    "NutritionValidationReport",
]
