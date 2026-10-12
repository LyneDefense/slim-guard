"""Typed continuity for record requests; not an authorization queue."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.features.health_records.patches import HealthRecordPatch
from slim_guard.features.health_records.references import RecordKind, RecordReference


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: RecordReference
    summary: str = Field(min_length=1, max_length=500)


class RecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["create", "update", "void", "restore"]
    record_kind: RecordKind
    selected_targets: tuple[RecordReference, ...] = ()
    candidate_targets: tuple[CandidateRecord, ...] = ()
    known_changes: HealthRecordPatch | None = None
    question_message_ref: str | None = None
    executed_operation_refs: tuple[str, ...] = ()
