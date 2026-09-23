"""Historical recognition artifact contracts used by admin correction APIs."""

from slim_guard.agents.dish_recognition.contracts import (
    ConfirmedDish,
    ConfirmedDishSet,
    DishCandidate,
    DishConfirmationSource,
    DishImageKind,
    DishRecognitionCorrection,
    DishRecognitionCorrectionItem,
    DishRecognitionResult,
    RecognizedDish,
)

__all__ = [
    "ConfirmedDish",
    "ConfirmedDishSet",
    "DishCandidate",
    "DishConfirmationSource",
    "DishImageKind",
    "DishRecognitionCorrection",
    "DishRecognitionCorrectionItem",
    "DishRecognitionResult",
    "RecognizedDish",
]
