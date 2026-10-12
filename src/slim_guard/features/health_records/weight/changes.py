"""A weight correction is one value with its unit, never two independent edits."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.features.health_records.changes import FieldChange, RecordPatch


class WeightValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: Decimal = Field(gt=0, allow_inf_nan=False)
    unit: Literal["kg", "jin", "lb"]


class WeightPatch(RecordPatch):
    kind: Literal["weight"] = "weight"
    measurement: FieldChange[WeightValue] | None = None
    condition: FieldChange[Literal["fasting", "post_meal", "unspecified"]] | None = None
