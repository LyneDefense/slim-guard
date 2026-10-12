import pytest
from pydantic import TypeAdapter, ValidationError

from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.patches import HealthRecordPatch
from slim_guard.features.health_records.provenance import FieldEvidence


@pytest.mark.parametrize("source", ["user_reported", "image_recognized", "context_inferred"])
def test_nonlegacy_values_require_evidence_references(source):
    with pytest.raises(ValidationError, match="evidence references"):
        FieldEvidence(source_kind=source)


@pytest.mark.parametrize("refs", [[""], ["   "], ["message-1", "message-1"]])
def test_empty_or_duplicate_evidence_references_rejected(refs):
    with pytest.raises(ValidationError):
        FieldEvidence(source_kind="user_reported", source_refs=refs)


def test_new_meal_item_identity_cannot_be_supplied_by_model():
    with pytest.raises(ValidationError):
        MealPatch.model_validate(
            {
                "food_changes": [
                    {
                        "action": "add_item",
                        "item_id": "claimed-id",
                        "name": "番茄炒蛋",
                        "evidence": {"source_kind": "image_recognized", "source_refs": ["image-1"]},
                    }
                ]
            }
        )


def test_duplicate_meal_item_identity_rejected():
    food = MealItem(item_id="F1", name="米饭")
    with pytest.raises(ValidationError, match="unique"):
        MealContent(foods=(food, food))


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_name_not_a_substitute_for_unknown(name):
    with pytest.raises(ValidationError):
        MealItem(item_id="F1", name=name)
    assert MealItem(item_id="F1", name=None).name is None


def test_free_json_path_not_accepted_as_patch():
    with pytest.raises(ValidationError):
        TypeAdapter(HealthRecordPatch).validate_python(
            {
                "kind": "meal",
                "path": "/foods/0/name",
                "value": "新菜名",
            }
        )


def test_inferred_time_cannot_be_labeled_user_reported():
    with pytest.raises(ValidationError, match="User-reported occurrence"):
        MealPatch.model_validate(
            {
                "occurrence": {
                    "value": {"precision": "date", "on": "2026-09-24", "basis": "user_reported"},
                    "evidence": {"source_kind": "context_inferred", "source_refs": ["message-1"]},
                }
            }
        )
