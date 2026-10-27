from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.patches import HealthRecordPatch
from slim_guard.features.health_records.persistence.evidence import validate_evidence
from slim_guard.features.health_records.provenance import FieldEvidence
from slim_guard.features.health_records.references import RecordError


@pytest.mark.parametrize("source", ["user_reported", "image_recognized", "context_inferred"])
def test_nonlegacy_values_require_evidence_references(source):
    with pytest.raises(ValidationError, match="evidence references"):
        FieldEvidence(source_kind=source)


def test_evidence_schema_explains_message_and_image_source_selection():
    evidence_schema = FieldEvidence.model_json_schema()

    source_refs_description = evidence_schema["properties"]["source_refs"]["description"]
    source_kind_description = evidence_schema["properties"]["source_kind"]["description"]
    assert "current-user-message" in source_refs_description
    assert "image_recognized" in source_refs_description
    assert "user_reported" in source_kind_description


@pytest.mark.anyio
async def test_image_asset_cannot_be_mislabeled_as_user_reported_evidence():
    class Session:
        async def scalar(self, statement):
            params = statement.compile().params
            reference = next(
                value for value in params.values() if value in {"message-1", "image-1"}
            )
            if reference == "message-1":
                return SimpleNamespace(item_type="user_message")
            return None

        async def get(self, _model, reference):
            return SimpleNamespace(user_id="user-1") if reference == "image-1" else None

    session = Session()
    await validate_evidence(
        session, "user-1", FieldEvidence(source_kind="user_reported", source_refs=("message-1",))
    )
    with pytest.raises(RecordError, match="user-message reference"):
        await validate_evidence(
            session, "user-1", FieldEvidence(source_kind="user_reported", source_refs=("image-1",))
        )
    with pytest.raises(RecordError, match="image-asset reference"):
        await validate_evidence(
            session,
            "user-1",
            FieldEvidence(source_kind="image_recognized", source_refs=("message-1",)),
        )


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
