"""Body-fat corrections use the same field lifecycle as other records."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from slim_guard.features.health_records.changes import FieldChange, RecordPatch


class BodyFatPatch(RecordPatch):
    kind: Literal["body_fat"] = "body_fat"
    measurement: FieldChange[Annotated[Decimal, Field(gt=0, le=75, allow_inf_nan=False)]] | None = (
        None
    )
