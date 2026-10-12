"""Typed field changes shared by record domains, not arbitrary JSON patches."""

from typing import Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.provenance import FieldEvidence, RecognitionConcern

Value = TypeVar("Value")


class FieldChange(BaseModel, Generic[Value]):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    value: Value
    evidence: FieldEvidence
    concern: RecognitionConcern | None = None


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    occurrence: FieldChange[Occurrence] | None = None

    @model_validator(mode="after")
    def occurrence_basis_matches_evidence(self) -> Self:
        if self.occurrence is None:
            return self
        basis = self.occurrence.value.basis
        source = self.occurrence.evidence.source_kind
        if basis == "user_reported" and source != "user_reported":
            raise ValueError("User-reported occurrence requires user evidence")
        if basis == "inferred" and source not in {"context_inferred", "image_recognized"}:
            raise ValueError("Inferred occurrence requires inference evidence")
        return self
