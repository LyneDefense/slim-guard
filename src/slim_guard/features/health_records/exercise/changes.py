"""Exercise edits only touch explicitly supplied fields."""

from typing import Annotated, Literal

from pydantic import Field

from slim_guard.features.health_records.changes import FieldChange, RecordPatch


class ExercisePatch(RecordPatch):
    kind: Literal["exercise"] = "exercise"
    activity_name: FieldChange[Annotated[str, Field(min_length=1, max_length=128)]] | None = None
    duration_minutes: FieldChange[Annotated[int, Field(ge=1, le=1440)] | None] | None = None
    steps: FieldChange[Annotated[int, Field(ge=0, le=200_000)] | None] | None = None
    distance_meters: FieldChange[Annotated[int, Field(ge=0, le=1_000_000)] | None] | None = None
    reported_energy_kcal: FieldChange[Annotated[int, Field(ge=0, le=20_000)] | None] | None = None
    note: FieldChange[Annotated[str, Field(min_length=1, max_length=1000)] | None] | None = None
