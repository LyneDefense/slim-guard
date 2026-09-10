from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from slim_guard.agents.contracts import Confidence, ContractModel


class DietGuidanceScope(StrEnum):
    WEIGHT_MANAGEMENT_GENERAL = "weight_management_general"
    CONSTRAINT_SPECIFIC = "constraint_specific"
    SAFETY_REFERRAL = "safety_referral"


class DishSuitability(StrEnum):
    SUITABLE = "suitable"
    SUITABLE_WITH_ADJUSTMENT = "suitable_with_adjustment"
    LIMIT = "limit"
    AVOID = "avoid"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class DietGuidanceReason(ContractModel):
    reason_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=1000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    citation_refs: tuple[str, ...] = Field(default=(), max_length=32)
    confidence: Confidence


class DietGuidanceAction(ContractModel):
    action_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=1000)
    basis_reason_ids: tuple[str, ...] = Field(min_length=1, max_length=16)


class DishSuitabilityAssessment(ContractModel):
    dish_ref: str = Field(pattern=r"^dish-[A-Za-z0-9_-]+$", max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    suitability: DishSuitability
    reasons: tuple[DietGuidanceReason, ...] = Field(default=(), max_length=16)
    actions: tuple[DietGuidanceAction, ...] = Field(default=(), max_length=8)
    hard_rule_refs: tuple[str, ...] = Field(default=(), max_length=16)
    user_constraint_refs: tuple[str, ...] = Field(default=(), max_length=16)
    uncertainty_note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        reason_ids = tuple(item.reason_id for item in self.reasons)
        if len(reason_ids) != len(set(reason_ids)):
            raise ValueError("Diet guidance reason IDs must be unique")
        action_ids = tuple(item.action_id for item in self.actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Diet guidance action IDs must be unique")
        unknown_reasons = {
            item
            for action in self.actions
            for item in action.basis_reason_ids
            if item not in set(reason_ids)
        }
        if unknown_reasons:
            raise ValueError("Diet guidance actions reference unknown reasons")
        if self.suitability is DishSuitability.AVOID and not (
            self.hard_rule_refs or self.user_constraint_refs
        ):
            raise ValueError("Avoid guidance requires a hard rule or user constraint")
        if self.suitability is DishSuitability.INSUFFICIENT_INFORMATION:
            if self.uncertainty_note is None:
                raise ValueError("Insufficient guidance requires an uncertainty note")
        elif not self.reasons:
            raise ValueError("A suitability conclusion requires at least one reason")
        return self


class DietGuidanceAssessment(ContractModel):
    schema_version: Literal["1"] = "1"
    scope: DietGuidanceScope
    dishes: tuple[DishSuitabilityAssessment, ...] = Field(min_length=1, max_length=20)
    meal_level_advice: tuple[str, ...] = Field(default=(), max_length=8)
    questions: tuple[str, ...] = Field(default=(), max_length=8)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=16)
    referral: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_dishes(self) -> Self:
        refs = tuple(item.dish_ref for item in self.dishes)
        if len(refs) != len(set(refs)):
            raise ValueError("Diet guidance dish references must be unique")
        if self.scope is DietGuidanceScope.SAFETY_REFERRAL and self.referral is None:
            raise ValueError("Safety referral guidance requires referral text")
        return self


__all__ = [
    "DietGuidanceAction",
    "DietGuidanceAssessment",
    "DietGuidanceReason",
    "DietGuidanceScope",
    "DishSuitability",
    "DishSuitabilityAssessment",
]
