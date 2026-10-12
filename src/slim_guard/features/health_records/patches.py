"""Public union of domain-owned, executable record patches."""

from typing import Annotated, Literal

from pydantic import Field

from slim_guard.features.health_records.body_fat.changes import BodyFatPatch
from slim_guard.features.health_records.changes import FieldChange, RecordPatch
from slim_guard.features.health_records.exercise.changes import ExercisePatch
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.weight.changes import WeightPatch


class StatusPatch(RecordPatch):
    kind: Literal["status"] = "status"
    status: FieldChange[Literal["active", "voided"]]


HealthRecordPatch = Annotated[
    MealPatch | ExercisePatch | WeightPatch | BodyFatPatch | StatusPatch,
    Field(discriminator="kind"),
]
