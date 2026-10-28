"""User-authored meal edits, independent of agents and chat messages."""

from typing import Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.features.health_records.changes import FieldChange
from slim_guard.features.health_records.content import MealContent
from slim_guard.features.health_records.meal.changes import (
    AddFood,
    MealItemChange,
    MealPatch,
    RemoveFood,
    ReorderFoods,
    ReplaceFoodName,
)
from slim_guard.features.health_records.provenance import FieldEvidence
from slim_guard.features.health_records.references import RecordError


class EditableFood(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)


class MealEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"]
    foods: tuple[EditableFood, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_existing_items(self) -> Self:
        ids = [food.item_id for food in self.foods if food.item_id]
        if len(ids) != len(set(ids)):
            raise ValueError("A meal item can only appear once")
        return self


def edit_patch(
    content: MealContent,
    request: MealEditRequest,
    *,
    source_id: str,
) -> tuple[MealPatch, tuple[str, ...]]:
    """Keep existing item identities/portions/photos; allocate new IDs once per operation."""
    evidence = FieldEvidence(source_kind="user_reported", source_refs=(source_id,))
    existing = {food.item_id: food for food in content.foods}
    retained = {food.item_id for food in request.foods if food.item_id}
    if retained - existing.keys():
        raise RecordError("A selected food does not belong to this meal")
    changes: list[MealItemChange] = [
        RemoveFood(action="remove_item", item_id=item_id, evidence=evidence)
        for item_id in existing
        if item_id not in retained
    ]
    new_ids: list[str] = []
    order: list[str] = []
    for index, food in enumerate(request.foods):
        if food.item_id:
            order.append(food.item_id)
            changes.append(
                ReplaceFoodName(
                    action="replace_name",
                    item_id=food.item_id,
                    name=food.name,
                    evidence=evidence,
                )
            )
        else:
            item_id = str(uuid5(NAMESPACE_URL, f"{source_id}/food/{index}"))
            new_ids.append(item_id)
            order.append(item_id)
            changes.append(AddFood(action="add_item", name=food.name, evidence=evidence))
    changes.append(ReorderFoods(action="reorder", item_ids=tuple(order), evidence=evidence))
    return MealPatch(
        meal_type=FieldChange(value=request.meal_type, evidence=evidence),
        food_changes=tuple(changes),
    ), tuple(new_ids)
