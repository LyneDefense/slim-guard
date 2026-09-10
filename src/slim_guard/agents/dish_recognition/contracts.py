from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from slim_guard.agent_models.gateway import ModelUsage
from slim_guard.agents.contracts import ContractModel, InvocationStatus


class DishImageKind(StrEnum):
    MEAL = "meal"
    NON_FOOD = "non_food"
    UNUSABLE = "unusable"


class DishConfirmationSource(StrEnum):
    USER_CONFIRMED = "user_confirmed"
    USER_TEXT = "user_text"
    HIGH_CONFIDENCE_VISUAL = "high_confidence_visual"


class DishCandidate(ContractModel):
    label: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0, le=1)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Dish candidate label cannot be blank")
        return normalized


class RecognizedDish(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    candidates: tuple[DishCandidate, ...] = Field(min_length=1, max_length=3)
    visible_ingredients: tuple[str, ...] = Field(default=(), max_length=20)
    preparation_candidates: tuple[str, ...] = Field(default=(), max_length=10)
    uncertainty_reasons: tuple[str, ...] = Field(default=(), max_length=10)
    requires_confirmation: bool

    @field_validator(
        "visible_ingredients",
        "preparation_candidates",
        "uncertainty_reasons",
    )
    @classmethod
    def validate_text_list(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split()) for item in value)
        if any(not item for item in normalized):
            raise ValueError("Dish recognition lists cannot contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Dish recognition lists cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        labels = tuple(item.label.casefold() for item in self.candidates)
        if len(labels) != len(set(labels)):
            raise ValueError("Dish candidates must have unique labels")
        scores = tuple(item.confidence for item in self.candidates)
        if scores != tuple(sorted(scores, reverse=True)):
            raise ValueError("Dish candidates must be sorted by confidence")
        if self.requires_confirmation and not self.uncertainty_reasons:
            raise ValueError("A dish requiring confirmation needs an uncertainty reason")
        return self


class DishRecognitionResult(ContractModel):
    schema_version: Literal["1"] = "1"
    asset_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    image_kind: DishImageKind
    quality_flags: tuple[str, ...] = Field(default=(), max_length=20)
    dishes: tuple[RecognizedDish, ...] = Field(default=(), max_length=20)
    suggested_question: str | None = Field(default=None, min_length=1, max_length=500)
    overall_requires_confirmation: bool
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("quality_flags")
    @classmethod
    def validate_quality_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split()) for item in value)
        if any(not item for item in normalized):
            raise ValueError("Quality flags cannot contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Quality flags cannot contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        refs = tuple(item.dish_ref for item in self.dishes)
        if len(refs) != len(set(refs)):
            raise ValueError("Recognized dish references must be unique")
        if self.image_kind is DishImageKind.MEAL and not self.dishes:
            raise ValueError("A meal recognition result requires at least one dish")
        if self.image_kind is not DishImageKind.MEAL and self.dishes:
            raise ValueError("A non-meal recognition result cannot contain dishes")
        needs_confirmation = any(item.requires_confirmation for item in self.dishes)
        if self.image_kind is DishImageKind.UNUSABLE:
            needs_confirmation = True
        if self.overall_requires_confirmation != needs_confirmation:
            raise ValueError("Overall confirmation must match dish and image uncertainty")
        if self.overall_requires_confirmation and self.suggested_question is None:
            raise ValueError("A result requiring confirmation needs one question")
        if not self.overall_requires_confirmation and self.suggested_question is not None:
            raise ValueError("A confirmed result cannot include a confirmation question")
        return self


class ConfirmedDish(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    name: str = Field(min_length=1, max_length=128)
    source: DishConfirmationSource
    recognition_confidence: float | None = Field(default=None, ge=0, le=1)
    user_evidence_ref: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source is DishConfirmationSource.USER_CONFIRMED:
            if self.user_evidence_ref is None:
                raise ValueError("User-confirmed dishes require a user evidence reference")
        elif self.user_evidence_ref is not None:
            raise ValueError("Only user-confirmed dishes may reference confirmation evidence")
        if self.source is DishConfirmationSource.HIGH_CONFIDENCE_VISUAL:
            if self.recognition_confidence is None:
                raise ValueError("Visual dishes require their original confidence")
        return self


class ConfirmedDishSet(ContractModel):
    schema_version: Literal["1"] = "1"
    source_artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    dishes: tuple[ConfirmedDish, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_refs(self) -> Self:
        refs = tuple(item.dish_ref for item in self.dishes)
        if len(refs) != len(set(refs)):
            raise ValueError("Confirmed dish references must be unique")
        if (
            any(
                item.source is DishConfirmationSource.HIGH_CONFIDENCE_VISUAL for item in self.dishes
            )
            and self.source_artifact_id is None
        ):
            raise ValueError("Visual confirmations require a recognition artifact")
        return self


class DishRecognitionCorrectionItem(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    corrected_name: str = Field(min_length=1, max_length=128)

    @field_validator("corrected_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Corrected dish name cannot be blank")
        return normalized


class DishRecognitionCorrection(ContractModel):
    """Append-only admin review; it never rewrites the recognition artifact."""

    schema_version: Literal["1"] = "1"
    recognition_artifact_id: str = Field(min_length=1, max_length=128)
    corrected_dishes: tuple[DishRecognitionCorrectionItem, ...] = Field(min_length=1, max_length=20)
    reviewer: str = Field(min_length=1, max_length=128)
    comment: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_refs(self) -> Self:
        refs = tuple(item.dish_ref for item in self.corrected_dishes)
        if len(refs) != len(set(refs)):
            raise ValueError("Dish correction references must be unique")
        return self


@dataclass(frozen=True, slots=True)
class DishRecognitionAgentResult:
    status: InvocationStatus
    recognition: DishRecognitionResult | None
    model_call_count: int
    total_token_count: int
    usage: ModelUsage = ModelUsage()
    failure_code: str | None = None


__all__ = [
    "ConfirmedDish",
    "ConfirmedDishSet",
    "DishCandidate",
    "DishConfirmationSource",
    "DishImageKind",
    "DishRecognitionCorrection",
    "DishRecognitionCorrectionItem",
    "DishRecognitionAgentResult",
    "DishRecognitionResult",
    "RecognizedDish",
]
