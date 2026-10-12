"""Typed current record content. Fields omitted from patches are never erased."""

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.features.health_records.body_fat.values import basis_points
from slim_guard.features.health_records.weight.values import weight_grams


class MealItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    item_id: str = Field(min_length=1, max_length=128, pattern=r"^[^/]+$")
    name: str | None = Field(min_length=1, max_length=128)
    portion: str | None = Field(default=None, min_length=1, max_length=128)


class MealContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["meal"] = "meal"
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = "unspecified"
    foods: tuple[MealItem, ...] = Field(max_length=20)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len({food.item_id for food in self.foods}) != len(self.foods):
            raise ValueError("Meal item identities must be unique")
        return self


class ExerciseContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["exercise"] = "exercise"
    activity_name: str = Field(min_length=1, max_length=128)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    steps: int | None = Field(default=None, ge=0, le=200_000)
    distance_meters: int | None = Field(default=None, ge=0, le=1_000_000)
    reported_energy_kcal: int | None = Field(default=None, ge=0, le=20_000)
    note: str | None = Field(default=None, max_length=1000)


class WeightContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["weight"] = "weight"
    weight_grams: int = Field(ge=10_000, le=500_000)
    original_value: Decimal = Field(gt=0)
    original_unit: Literal["kg", "jin", "lb"]
    condition: Literal["fasting", "post_meal", "unspecified"] = "unspecified"

    @model_validator(mode="after")
    def consistent_measurement(self) -> Self:
        if self.weight_grams != weight_grams(self.original_value, self.original_unit):
            raise ValueError("Canonical weight must match its original value and unit")
        return self


class BodyFatContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["body_fat"] = "body_fat"
    basis_points: int = Field(ge=100, le=7500)
    original_value: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def consistent_measurement(self) -> Self:
        if self.basis_points != basis_points(self.original_value):
            raise ValueError("Canonical body-fat must match its original value")
        return self
