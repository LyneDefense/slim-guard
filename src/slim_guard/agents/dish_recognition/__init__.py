"""Typed meal-image recognition specialist."""

from slim_guard.agents.dish_recognition.agent import (
    DishRecognitionAgent,
    DishRecognitionPolicy,
)
from slim_guard.agents.dish_recognition.contracts import (
    ConfirmedDish,
    ConfirmedDishSet,
    DishCandidate,
    DishConfirmationSource,
    DishImageKind,
    DishRecognitionAgentResult,
    DishRecognitionCorrection,
    DishRecognitionCorrectionItem,
    DishRecognitionResult,
    RecognizedDish,
)
from slim_guard.agents.dish_recognition.prompt import (
    DISH_RECOGNITION_PROMPT,
    DISH_RECOGNITION_PROMPT_VERSION,
    DISH_RECOGNITION_SCHEMA_VERSION,
)

__all__ = [
    "ConfirmedDish",
    "ConfirmedDishSet",
    "DishRecognitionAgent",
    "DishRecognitionPolicy",
    "DISH_RECOGNITION_PROMPT",
    "DISH_RECOGNITION_PROMPT_VERSION",
    "DISH_RECOGNITION_SCHEMA_VERSION",
    "DishCandidate",
    "DishConfirmationSource",
    "DishImageKind",
    "DishRecognitionCorrection",
    "DishRecognitionCorrectionItem",
    "DishRecognitionAgentResult",
    "DishRecognitionResult",
    "RecognizedDish",
]
