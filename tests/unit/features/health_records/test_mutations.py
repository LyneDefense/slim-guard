from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from pydantic import ValidationError

from slim_guard.features.health_records.body_fat.changes import BodyFatPatch
from slim_guard.features.health_records.content import (
    BodyFatContent,
    ExerciseContent,
    MealContent,
    MealItem,
    WeightContent,
)
from slim_guard.features.health_records.exercise.changes import ExercisePatch
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.mutations import apply_record_patch
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.patches import StatusPatch
from slim_guard.features.health_records.provenance import FieldProvenance, RecognitionIssue
from slim_guard.features.health_records.references import (
    RecordConflict,
    RecordError,
    RecordReference,
)
from slim_guard.features.health_records.views import RecordView
from slim_guard.features.health_records.weight.changes import WeightPatch

NOW = datetime(2026, 9, 24, 5, tzinfo=UTC)
USER = {"source_kind": "user_reported", "source_refs": ["message-new"]}
IMAGE = {"source_kind": "image_recognized", "source_refs": ["image-1"]}


@pytest.fixture
def meal() -> RecordView:
    return RecordView(
        ref=RecordReference(kind="meal", record_id="M1", revision=3),
        status="active",
        occurrence=Occurrence(),
        recorded_at=NOW,
        updated_at=NOW,
        content=MealContent(
            foods=(
                MealItem(item_id="F1", name="番茄炒蛋"),
                MealItem(item_id="F2", name="土豆肉丝", portion="一份"),
            )
        ),
        source_refs=("image-1",),
        field_provenance=tuple(
            FieldProvenance(
                field_ref=f"foods/{item}/name",
                value_revision=1,
                source_kind="image_recognized",
                source_refs=("image-1",),
            )
            for item in ("F1", "F2")
        ),
        recognition_issues=tuple(
            RecognitionIssue(
                issue_id=f"I{index}",
                field_ref=f"foods/{item}/name",
                value_revision=1,
                reason="部分遮挡",
                user_message="这道菜名称可能不准确",
            )
            for index, item in enumerate(("F1", "F2"), 1)
        ),
    )


def apply(meal: RecordView, *changes: dict):
    ids = count(1)
    return apply_record_patch(
        meal,
        MealPatch.model_validate({"food_changes": changes}),
        expected_revision=meal.ref.revision,
        now=NOW + timedelta(seconds=1),
        new_id=lambda: f"generated-{next(ids)}",
    )


@pytest.mark.parametrize(
    "action,name",
    [
        ("replace_name", "青椒肉丝"),
        ("reject_name", None),
        ("acknowledge_name", "土豆肉丝"),
    ],
)
def test_name_correction_preserves_identity_and_clears_only_own_issue(meal, action, name):
    change = {"action": action, "item_id": "F2", "evidence": USER}
    if action == "replace_name":
        change["name"] = name
    result = apply(meal, change)
    assert result.record.ref.record_id == "M1"
    assert result.record.ref.revision == 4
    assert result.record.content.foods[1].item_id == "F2"
    assert result.record.content.foods[1].name == name
    assert result.record.content.foods[1].portion == "一份"
    assert result.record.content.foods[0] == meal.content.foods[0]
    assert result.cleared_issue_ids == ("I2",)
    assert [i.issue_id for i in result.record.recognition_issues] == ["I1"]
    assert result.changed_fields == ("foods/F2/name",)
    source = next(p for p in result.record.field_provenance if p.field_ref == "foods/F2/name")
    assert source.source_kind == "user_reported"
    assert source.value_revision == 4
    assert meal.content.foods[1].name == "土豆肉丝"
    assert len(meal.recognition_issues) == 2


def test_reject_then_replace_keeps_original_item(meal):
    rejected = apply(meal, {"action": "reject_name", "item_id": "F2", "evidence": USER}).record
    result = apply(
        rejected,
        {
            "action": "replace_name",
            "item_id": "F2",
            "name": "青椒肉丝",
            "evidence": USER,
        },
    )
    assert result.record.content.foods[1].item_id == "F2"
    assert result.record.content.foods[1].name == "青椒肉丝"
    assert result.record.ref.revision == 5
    assert result.cleared_issue_ids == ()


def test_portion_clear_is_distinct_from_omission(meal):
    result = apply(
        meal, {"action": "set_portion", "item_id": "F2", "portion": None, "evidence": USER}
    )
    assert result.record.content.foods[1].portion is None
    assert result.record.content.foods[1].name == "土豆肉丝"
    assert result.record.recognition_issues == meal.recognition_issues
    with pytest.raises(ValidationError):
        apply(meal, {"action": "set_portion", "item_id": "F2", "evidence": USER})


def test_remove_not_reject_drops_item_and_own_metadata(meal):
    result = apply(meal, {"action": "remove_item", "item_id": "F2", "evidence": USER})
    assert [food.item_id for food in result.record.content.foods] == ["F1"]
    assert result.removed_item_ids == ("F2",)
    assert result.cleared_issue_ids == ("I2",)
    assert all("F2" not in entry.field_ref for entry in result.record.field_provenance)
    assert "message-new" in result.record.source_refs


def test_reorder_then_correct_uses_identity_not_position(meal):
    result = apply(
        meal,
        {"action": "reorder", "item_ids": ["F2", "F1"], "evidence": USER},
        {"action": "replace_name", "item_id": "F2", "name": "青椒肉丝", "evidence": USER},
    )
    assert result.record.content.foods[0].name == "青椒肉丝"
    assert result.record.content.foods[1].name == "番茄炒蛋"
    assert result.record.ref.revision == 4


@pytest.mark.parametrize("item_ids", [["F1"], ["F1", "F3"], ["F1", "F1"]])
def test_reorder_requires_exact_membership(meal, item_ids):
    with pytest.raises((RecordError, ValidationError)):
        apply(meal, {"action": "reorder", "item_ids": item_ids, "evidence": USER})


def test_add_assigns_id_and_saves_specific_concern(meal):
    result = apply(
        meal,
        {
            "action": "add_item",
            "name": "花菜炒肉",
            "evidence": IMAGE,
            "concern": {"reason": "肉类被遮挡", "user_message": "肉的种类可能不准确"},
        },
    )
    food = result.record.content.foods[-1]
    assert food.item_id == "generated-1"
    assert len(result.record.content.foods) == 3
    issue = result.record.recognition_issues[-1]
    assert issue.field_ref == "foods/generated-1/name"
    assert issue.value_revision == 4
    assert result.record.status == "active"


def test_later_image_cannot_restore_user_rejected_name(meal):
    rejected = apply(meal, {"action": "reject_name", "item_id": "F2", "evidence": USER}).record
    with pytest.raises(RecordConflict, match="user-reported"):
        apply(
            rejected,
            {"action": "replace_name", "item_id": "F2", "name": "土豆肉丝", "evidence": IMAGE},
        )


def test_failure_in_later_change_does_not_mutate_input(meal):
    before = meal.model_dump_json()
    with pytest.raises(RecordError):
        apply(
            meal,
            {"action": "reject_name", "item_id": "F2", "evidence": USER},
            {"action": "replace_name", "item_id": "not-here", "name": "汤", "evidence": USER},
        )
    assert meal.model_dump_json() == before


def test_stale_revision_rejected_before_changes(meal):
    with pytest.raises(RecordConflict):
        apply_record_patch(meal, MealPatch(), expected_revision=2, now=NOW)


def test_empty_patch_does_not_advance_revision(meal):
    result = apply(meal)
    assert not result.changed
    assert result.record == meal


def test_same_acknowledgement_does_not_advance_again(meal):
    change = {"action": "acknowledge_name", "item_id": "F2", "evidence": USER}
    result = apply(meal, change)
    assert result.changed
    assert not apply(result.record, change).changed


def test_record_kind_checked(meal):
    with pytest.raises(RecordError, match="kind"):
        apply_record_patch(meal, ExercisePatch(), expected_revision=3, now=NOW)


def test_void_and_restore_keep_identity_and_metadata(meal):
    patch = StatusPatch.model_validate({"status": {"value": "voided", "evidence": USER}})
    result = apply_record_patch(meal, patch, expected_revision=3, now=NOW)
    assert result.record.status == "voided"
    assert result.record.content == meal.content
    with pytest.raises(RecordError, match="Restore"):
        apply(result.record, {"action": "reject_name", "item_id": "F2", "evidence": USER})
    restored = apply_record_patch(
        result.record,
        StatusPatch.model_validate({"status": {"value": "active", "evidence": USER}}),
        expected_revision=4,
        now=NOW,
    )
    assert restored.record.ref.record_id == "M1"
    assert restored.record.ref.revision == 5
    assert restored.record.recognition_issues == meal.recognition_issues


@pytest.mark.parametrize("action", ["reject_name", "acknowledge_name", "remove_item"])
def test_user_only_edits_reject_visual_evidence(meal, action):
    with pytest.raises(RecordError, match="user evidence"):
        apply(meal, {"action": action, "item_id": "F2", "evidence": IMAGE})


def test_cannot_acknowledge_unnamed_item(meal):
    record = apply(meal, {"action": "reject_name", "item_id": "F2", "evidence": USER}).record
    with pytest.raises(RecordError, match="unnamed"):
        apply(record, {"action": "acknowledge_name", "item_id": "F2", "evidence": USER})


@pytest.mark.parametrize(
    "content,patch,expected",
    [
        (
            ExerciseContent(activity_name="步行", duration_minutes=80, steps=5000),
            ExercisePatch.model_validate({"duration_minutes": {"value": 60, "evidence": USER}}),
            {"activity_name": "步行", "duration_minutes": 60, "steps": 5000},
        ),
        (
            WeightContent(weight_grams=80000, original_value=Decimal("80"), original_unit="kg"),
            WeightPatch.model_validate(
                {
                    "measurement": {
                        "value": {"value": "156", "unit": "jin"},
                        "evidence": USER,
                    }
                }
            ),
            {"weight_grams": 78000, "original_unit": "jin"},
        ),
        (
            BodyFatContent(basis_points=2300, original_value=Decimal("23")),
            BodyFatPatch.model_validate({"measurement": {"value": "24.1", "evidence": USER}}),
            {"basis_points": 2410},
        ),
    ],
)
def test_shared_revision_and_typed_domain_changes(content, patch, expected):
    record = RecordView(
        ref=RecordReference(kind=content.kind, record_id="R1", revision=1),
        status="active",
        occurrence=Occurrence(),
        recorded_at=NOW,
        updated_at=NOW,
        content=content,
    )
    result = apply_record_patch(record, patch, expected_revision=1, now=NOW)
    assert result.record.ref.revision == 2
    assert result.record.ref.record_id == "R1"
    for field, value in expected.items():
        assert getattr(result.record.content, field) == value


def test_meal_type_has_evidence_but_no_time_or_upload_rule(meal):
    result = apply_record_patch(
        meal,
        MealPatch.model_validate(
            {
                "meal_type": {"value": "breakfast", "evidence": USER},
                "occurrence": {
                    "value": {
                        "precision": "date",
                        "on": "2026-09-23",
                        "timezone": "Asia/Shanghai",
                        "basis": "user_reported",
                    },
                    "evidence": USER,
                },
            }
        ),
        expected_revision=3,
        now=NOW,
    )
    assert result.record.content.meal_type == "breakfast"
    assert result.record.occurrence.at is None
    assert str(result.record.occurrence.on) == "2026-09-23"
    assert result.record.recognition_issues == meal.recognition_issues


def test_old_issue_cannot_be_attached_to_new_field_version(meal):
    value = meal.model_dump()
    value["field_provenance"][0]["value_revision"] = 2
    with pytest.raises(ValidationError, match="current field value"):
        RecordView.model_validate(value)
