from typing import Literal

from pydantic import Field

from slim_guard.features.health_records.body_fat.changes import BodyFatPatch
from slim_guard.features.health_records.commands import TaskCompletion
from slim_guard.features.health_records.exercise.changes import ExercisePatch
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.patches import HealthRecordPatch, StatusPatch
from slim_guard.features.health_records.provenance import FieldEvidence, RecognitionConcern
from slim_guard.features.health_records.queries import RecordQuery
from slim_guard.features.health_records.references import RecordKind
from slim_guard.features.health_records.weight.changes import WeightPatch
from slim_guard.tools.contracts import ToolArguments


class CreateArguments(ToolArguments):
    occurrence: Occurrence = Field(default_factory=Occurrence)
    evidence: FieldEvidence


class FoodInput(ToolArguments):
    name: str = Field(min_length=1, max_length=128)
    portion: str | None = None
    evidence: FieldEvidence | None = None
    concern: RecognitionConcern | None = None


class CreateMealArguments(CreateArguments):
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = "unspecified"
    meal_type_evidence: FieldEvidence | None = None
    foods: list[FoodInput] = Field(min_length=1, max_length=20)
    note: str | None = None


class CreateExerciseArguments(CreateArguments):
    activity_name: str
    duration_minutes: int | None = None
    steps: int | None = None
    distance_meters: int | None = None
    reported_energy_kcal: int | None = None
    note: str | None = None


class CreateWeightArguments(CreateArguments):
    value: float = Field(gt=0)
    unit: Literal["kg", "jin", "lb"] = "kg"
    condition: Literal["fasting", "post_meal", "unspecified"] = "unspecified"


class CreateBodyFatArguments(CreateArguments):
    value: float = Field(gt=0, le=75)


class UpdateArguments(ToolArguments):
    record_id: str
    expected_revision: int = Field(ge=1)
    patch: HealthRecordPatch
    complete_tasks: list[TaskCompletion] = Field(default_factory=list)


class UpdateMealArguments(UpdateArguments):
    patch: MealPatch


class UpdateExerciseArguments(UpdateArguments):
    patch: ExercisePatch


class UpdateWeightArguments(UpdateArguments):
    patch: WeightPatch


class UpdateBodyFatArguments(UpdateArguments):
    patch: BodyFatPatch


class UpdateStatusArguments(UpdateArguments):
    record_kind: RecordKind
    patch: StatusPatch


class SearchArguments(ToolArguments):
    query: RecordQuery


class GetArguments(ToolArguments):
    kind: RecordKind
    record_id: str
