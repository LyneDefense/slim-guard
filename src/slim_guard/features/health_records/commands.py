from pydantic import BaseModel, ConfigDict, Field

from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.patches import HealthRecordPatch
from slim_guard.features.health_records.provenance import FieldProvenance, RecognitionIssue
from slim_guard.features.health_records.references import RecordReference
from slim_guard.features.health_records.views import RecordContent, RecordView


class RecordWriteContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str
    thread_id: str
    turn_id: str
    source_item_id: str | None
    tool_call_id: str
    operation_id: str = Field(min_length=1, max_length=128)


class CreateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: RecordContent
    occurrence: Occurrence
    field_provenance: tuple[FieldProvenance, ...] = ()
    recognition_issues: tuple[RecognitionIssue, ...] = ()


class TaskCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    expected_revision: int = Field(ge=1)


class UpdateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: RecordReference
    patch: HealthRecordPatch
    complete_tasks: tuple[TaskCompletion, ...] = ()


class RecordReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    created: bool
    changed: bool
    record: RecordView
    changed_fields: tuple[str, ...] = ()
    cleared_issue_ids: tuple[str, ...] = ()
    task_updates: tuple[str, ...] = ()
