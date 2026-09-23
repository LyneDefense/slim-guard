from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=500)]
TaskStatus = Literal["open", "resolved", "cancelled", "expired"]


class TaskEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_item_id: str
    excerpt: str
    # Model interpretation, not a new user-authored fact or an authorization grant.
    interpretation: str


class TaskRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    thread_id: str
    revision: int
    status: TaskStatus
    objective: str
    unresolved: tuple[str, ...]
    evidence: tuple[TaskEvidence, ...]
    source_item_ids: tuple[str, ...]
    attachment_ids: tuple[str, ...]
    source_turn_id: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


class TaskWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    source_turn_id: str = Field(min_length=1)
    source_item_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    objective: str = Field(min_length=1, max_length=300)
    unresolved: tuple[Text, ...] = Field(max_length=20)
    status: Literal["open", "resolved", "cancelled"] = "open"
    evidence_excerpt: str = Field(max_length=512)
    interpretation: str = Field(min_length=1, max_length=1000)
    source_item_ids: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_transition_shape(self) -> TaskWrite:
        if (self.task_id is None) != (self.expected_revision is None):
            raise ValueError("Updating a task requires its id and current revision")
        if self.task_id is None and self.status != "open":
            raise ValueError("New tasks start open")
        if self.status == "resolved" and self.unresolved:
            raise ValueError("Resolved tasks cannot retain unresolved items")
        if self.status == "open" and not self.unresolved:
            raise ValueError("Open tasks need an unfinished item")
        return self


class TaskError(ValueError):
    """Safe contract error suitable for model feedback."""


class TaskConflict(TaskError):
    """Reload current state before proposing another update."""
