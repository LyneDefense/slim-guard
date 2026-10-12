"""Stable, user-scoped identities shared by record consumers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RecordKind = Literal["meal", "exercise", "weight", "body_fat"]
RecordStatus = Literal["active", "voided"]


class RecordReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: RecordKind
    record_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


class RecordError(ValueError):
    """A rejected command has no committed effect."""


class RecordConflict(RecordError):
    """Reload authoritative state; do not overwrite another change."""
