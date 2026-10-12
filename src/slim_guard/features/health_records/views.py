"""Current read model shared by tools, context, analysis and presentation."""

from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from slim_guard.features.health_records.content import (
    BodyFatContent,
    ExerciseContent,
    MealContent,
    WeightContent,
)
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.provenance import FieldProvenance, RecognitionIssue
from slim_guard.features.health_records.references import RecordReference, RecordStatus

RecordContent = Annotated[
    MealContent | ExerciseContent | WeightContent | BodyFatContent,
    Field(discriminator="kind"),
]


class RecordView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: RecordReference
    status: RecordStatus
    occurrence: Occurrence
    recorded_at: AwareDatetime
    updated_at: AwareDatetime
    content: RecordContent
    source_refs: tuple[str, ...] = ()
    field_provenance: tuple[FieldProvenance, ...] = ()
    recognition_issues: tuple[RecognitionIssue, ...] = ()

    @model_validator(mode="after")
    def validate_current_state(self) -> Self:
        if self.content.kind != self.ref.kind:
            raise ValueError("Record reference and content must have the same kind")
        if self.updated_at < self.recorded_at:
            raise ValueError("Record update cannot precede creation")
        fields = self.field_names()
        provenance = {entry.field_ref: entry for entry in self.field_provenance}
        if len(provenance) != len(self.field_provenance):
            raise ValueError("A field has exactly one current provenance entry")
        for entry in self.field_provenance:
            if entry.field_ref not in fields or entry.value_revision > self.ref.revision:
                raise ValueError("Provenance must refer to a current field and version")
        issue_ids = {issue.issue_id for issue in self.recognition_issues}
        if len(issue_ids) != len(self.recognition_issues):
            raise ValueError("Recognition issue identities must be unique")
        for issue in self.recognition_issues:
            current_field = provenance.get(issue.field_ref)
            if current_field is None or issue.value_revision != current_field.value_revision:
                raise ValueError("Recognition issue must refer to the current field value")
        return self

    def field_names(self) -> set[str]:
        fields = {"occurrence", "status"}
        if isinstance(self.content, MealContent):
            fields.update(("meal_type", "note", "foods/order"))
            for food in self.content.foods:
                fields.update((f"foods/{food.item_id}/name", f"foods/{food.item_id}/portion"))
        elif isinstance(self.content, ExerciseContent):
            fields.update(type(self.content).model_fields.keys() - {"kind"})
        elif isinstance(self.content, WeightContent):
            fields.update(("measurement", "condition"))
        else:
            fields.add("measurement")
        return fields


class RecordChangeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: RecordView
    changed_fields: tuple[str, ...]
    removed_item_ids: tuple[str, ...] = ()
    cleared_issue_ids: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.changed_fields)
