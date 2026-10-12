"""Versioned field evidence and recognition issues; never a confirmation gate."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal["user_reported", "image_recognized", "context_inferred", "unknown", "legacy"]


class FieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_kind: SourceKind
    source_refs: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        default=(),
        max_length=20,
    )
    explanation: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if self.source_kind not in {"unknown", "legacy"} and not self.source_refs:
            raise ValueError("Reported or inferred values require evidence references")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("Evidence references must be unique")
        return self


class FieldProvenance(FieldEvidence):
    field_ref: str = Field(min_length=1, max_length=200)
    value_revision: int = Field(ge=1)


class RecognitionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    issue_id: str = Field(min_length=1, max_length=128)
    field_ref: str = Field(min_length=1, max_length=200)
    value_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    user_message: str = Field(min_length=1, max_length=500)


class RecognitionConcern(BaseModel):
    """Model-authored concern; identity and field version belong to persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=500)
    user_message: str = Field(min_length=1, max_length=500)
