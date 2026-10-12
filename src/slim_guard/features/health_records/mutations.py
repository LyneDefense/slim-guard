"""Pure record mutation boundary; persistence owns transactions and ownership checks."""

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from slim_guard.features.health_records.body_fat.changes import BodyFatPatch
from slim_guard.features.health_records.body_fat.values import basis_points
from slim_guard.features.health_records.content import (
    BodyFatContent,
    ExerciseContent,
    MealContent,
    WeightContent,
)
from slim_guard.features.health_records.exercise.changes import ExercisePatch
from slim_guard.features.health_records.field_lifecycle import FieldLifecycle
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.meal.mutations import apply_meal_patch
from slim_guard.features.health_records.patches import HealthRecordPatch, StatusPatch
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.features.health_records.views import RecordChangeResult, RecordView
from slim_guard.features.health_records.weight.changes import WeightPatch
from slim_guard.features.health_records.weight.values import weight_grams


def apply_record_patch(
    record: RecordView,
    patch: HealthRecordPatch,
    *,
    expected_revision: int,
    now: datetime,
    new_id: Callable[[], str] = lambda: str(uuid4()),
) -> RecordChangeResult:
    """Return a validated draft; failures never mutate the caller's snapshot.

    This function does not authorize a user or claim a database commit. The
    repository must compare-and-swap the revision again within its transaction.
    """
    if expected_revision != record.ref.revision:
        raise RecordConflict("Record revision changed; reload before forming a new command")
    if now.utcoffset() is None or now < record.updated_at:
        raise RecordError("Mutation time must be aware and not precede the current revision")
    if patch.kind != "status" and patch.kind != record.ref.kind:
        raise RecordError("Patch kind does not match the target record")
    if record.status != "active" and not isinstance(patch, StatusPatch):
        raise RecordError("Restore a voided record before editing its contents")
    fields = FieldLifecycle(record, new_id)
    content = record.content
    status = record.status
    occurrence = record.occurrence
    if patch.occurrence is not None:
        change = patch.occurrence
        fields.change("occurrence", occurrence, change.value, change.evidence, change.concern)
        occurrence = change.value
    if isinstance(patch, MealPatch) and isinstance(content, MealContent):
        content = apply_meal_patch(content, patch, fields, new_id)
    elif isinstance(patch, ExercisePatch) and isinstance(content, ExerciseContent):
        values = content.model_dump()
        for name in type(content).model_fields.keys() - {"kind"}:
            change = getattr(patch, name)
            if change is not None:
                fields.change(name, values[name], change.value, change.evidence, change.concern)
                values[name] = change.value
        content = ExerciseContent.model_validate(values)
    elif isinstance(patch, WeightPatch) and isinstance(content, WeightContent):
        values = content.model_dump()
        if patch.measurement is not None:
            weight_change = patch.measurement
            fields.change(
                "measurement",
                (content.original_value, content.original_unit),
                (weight_change.value.value, weight_change.value.unit),
                weight_change.evidence,
                weight_change.concern,
            )
            values.update(
                original_value=weight_change.value.value,
                original_unit=weight_change.value.unit,
                weight_grams=weight_grams(weight_change.value.value, weight_change.value.unit),
            )
        if patch.condition is not None:
            condition = patch.condition
            fields.change(
                "condition",
                content.condition,
                condition.value,
                condition.evidence,
                condition.concern,
            )
            values["condition"] = condition.value
        content = WeightContent.model_validate(values)
    elif isinstance(patch, BodyFatPatch) and isinstance(content, BodyFatContent):
        if patch.measurement is not None:
            fat_change = patch.measurement
            fields.change(
                "measurement",
                content.original_value,
                fat_change.value,
                fat_change.evidence,
                fat_change.concern,
            )
            content = BodyFatContent(
                basis_points=basis_points(fat_change.value),
                original_value=fat_change.value,
            )
    elif isinstance(patch, StatusPatch):
        if patch.status.evidence.source_kind != "user_reported":
            raise RecordError("Voiding or restoring requires user evidence")
        fields.change(
            "status",
            status,
            patch.status.value,
            patch.status.evidence,
            patch.status.concern,
        )
        status = patch.status.value
    if not fields.changed_fields:
        return RecordChangeResult(record=record, changed_fields=())
    values = record.model_dump()
    values.update(
        ref=record.ref.model_copy(update={"revision": record.ref.revision + 1}),
        status=status,
        occurrence=occurrence,
        content=content,
        updated_at=now,
        source_refs=tuple(sorted(fields.source_refs)),
        field_provenance=tuple(fields.provenance.values()),
        recognition_issues=tuple(fields.issues.values()),
    )
    return RecordChangeResult(
        record=RecordView.model_validate(values),
        changed_fields=tuple(sorted(fields.changed_fields)),
        removed_item_ids=tuple(sorted(fields.removed_item_ids)),
        cleared_issue_ids=tuple(sorted(fields.cleared_issue_ids)),
    )
