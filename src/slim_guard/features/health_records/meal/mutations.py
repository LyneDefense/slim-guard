"""Meal-local changes. Identity is stable across renaming and reordering."""

from collections.abc import Callable

from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.field_lifecycle import FieldLifecycle
from slim_guard.features.health_records.meal.changes import (
    AcknowledgeFoodName,
    AddFood,
    ChangeFoodPortion,
    MealPatch,
    RejectFoodName,
    RemoveFood,
    ReorderFoods,
    ReplaceFoodName,
)
from slim_guard.features.health_records.references import RecordError


def apply_meal_patch(
    content: MealContent,
    patch: MealPatch,
    fields: FieldLifecycle,
    new_id: Callable[[], str],
) -> MealContent:
    data = content.model_dump()
    for name in ("meal_type", "note"):
        change = getattr(patch, name)
        if change is not None:
            fields.change(name, data[name], change.value, change.evidence, change.concern)
            data[name] = change.value
    foods = list(content.foods)
    allocated_ids = {food.item_id for food in foods}
    for change in patch.food_changes:
        if isinstance(change, AddFood):
            item_id = new_id()
            if item_id in allocated_ids:
                raise RecordError("Generated meal item identity is not unique")
            allocated_ids.add(item_id)
            food = MealItem(item_id=item_id, name=change.name, portion=change.portion)
            fields.change(
                f"foods/{item_id}/name",
                None,
                food.name,
                change.evidence,
                change.concern,
            )
            if change.portion is not None:
                fields.change(f"foods/{item_id}/portion", None, food.portion, change.evidence)
            foods.append(food)
            continue
        if isinstance(change, ReorderFoods):
            by_id = {food.item_id: food for food in foods}
            if set(change.item_ids) != set(by_id):
                raise RecordError("Reordering must include every current meal item exactly once")
            fields.change(
                "foods/order",
                tuple(by_id),
                change.item_ids,
                change.evidence,
            )
            foods = [by_id[item_id] for item_id in change.item_ids]
            continue
        index = next((i for i, food in enumerate(foods) if food.item_id == change.item_id), None)
        if index is None:
            raise RecordError("Meal item does not exist in this record")
        food = foods[index]
        prefix = f"foods/{food.item_id}/"
        updated = food.model_dump()
        if isinstance(change, RemoveFood):
            fields.remove_item(food.item_id, change.evidence)
            foods.pop(index)
            continue
        if isinstance(change, ChangeFoodPortion):
            fields.change(prefix + "portion", food.portion, change.portion, change.evidence)
            updated["portion"] = change.portion
        elif isinstance(change, ReplaceFoodName):
            fields.change(prefix + "name", food.name, change.name, change.evidence, change.concern)
            updated["name"] = change.name
        elif isinstance(change, (RejectFoodName, AcknowledgeFoodName)):
            if change.evidence.source_kind != "user_reported":
                raise RecordError("Rejecting or acknowledging a name requires user evidence")
            if isinstance(change, AcknowledgeFoodName) and food.name is None:
                raise RecordError("An unnamed item has no name to acknowledge")
            new_name = None if isinstance(change, RejectFoodName) else food.name
            fields.change(prefix + "name", food.name, new_name, change.evidence)
            updated["name"] = new_name
        foods[index] = MealItem.model_validate(updated)
    data["foods"] = tuple(foods)
    return MealContent.model_validate(data)
