"""Typed meal edits. The model chooses meaning; these types protect stored state."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.features.health_records.changes import FieldChange, RecordPatch
from slim_guard.features.health_records.provenance import FieldEvidence, RecognitionConcern


class FoodChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AddFood(FoodChange):
    action: Literal["add_item"]
    name: str = Field(min_length=1, max_length=128)
    portion: str | None = Field(default=None, min_length=1, max_length=128)
    evidence: FieldEvidence
    concern: RecognitionConcern | None = None


class ReplaceFoodName(FoodChange):
    action: Literal["replace_name"]
    item_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    evidence: FieldEvidence
    concern: RecognitionConcern | None = None


class RejectFoodName(FoodChange):
    action: Literal["reject_name"]
    item_id: str = Field(min_length=1, max_length=128)
    evidence: FieldEvidence


class AcknowledgeFoodName(FoodChange):
    action: Literal["acknowledge_name"]
    item_id: str = Field(min_length=1, max_length=128)
    evidence: FieldEvidence


class ChangeFoodPortion(FoodChange):
    action: Literal["set_portion"]
    item_id: str = Field(min_length=1, max_length=128)
    portion: str | None = Field(max_length=128, min_length=1)
    evidence: FieldEvidence


class RemoveFood(FoodChange):
    action: Literal["remove_item"]
    item_id: str = Field(min_length=1, max_length=128)
    evidence: FieldEvidence


class ReorderFoods(FoodChange):
    action: Literal["reorder"]
    item_ids: tuple[str, ...] = Field(max_length=20)
    evidence: FieldEvidence

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("Food order cannot repeat an item")
        return self


MealItemChange = Annotated[
    AddFood
    | ReplaceFoodName
    | RejectFoodName
    | AcknowledgeFoodName
    | ChangeFoodPortion
    | RemoveFood
    | ReorderFoods,
    Field(discriminator="action"),
]


class MealPatch(RecordPatch):
    kind: Literal["meal"] = "meal"
    meal_type: (
        FieldChange[Literal["breakfast", "lunch", "dinner", "snack", "unspecified"]] | None
    ) = None
    note: FieldChange[Annotated[str, Field(min_length=1, max_length=1000)] | None] | None = None
    photo_asset_ids: FieldChange[Annotated[tuple[str, ...], Field(max_length=8)]] | None = None
    food_changes: tuple[MealItemChange, ...] = Field(default=(), max_length=41)
