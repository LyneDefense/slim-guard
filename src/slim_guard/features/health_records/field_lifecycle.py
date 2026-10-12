"""An isolated mutation draft: no database writes and no intent classification."""

from collections.abc import Callable
from typing import Any

from slim_guard.features.health_records.provenance import (
    FieldEvidence,
    FieldProvenance,
    RecognitionConcern,
    RecognitionIssue,
)
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.features.health_records.views import RecordView


class FieldLifecycle:
    """Advance field versions and invalidate only issues for affected values.

    Source ownership is checked by the transactional service before applying the
    draft. This guard additionally prevents an inferred value from replacing a
    user-reported value, even if a late recognizer fetched the latest revision.
    """

    def __init__(self, record: RecordView, new_id: Callable[[], str]) -> None:
        self.provenance = {entry.field_ref: entry for entry in record.field_provenance}
        self.issues = {entry.issue_id: entry for entry in record.recognition_issues}
        self.changed_fields: set[str] = set()
        self.removed_item_ids: set[str] = set()
        self.cleared_issue_ids: set[str] = set()
        self.source_refs = set(record.source_refs)
        self._revision = record.ref.revision + 1
        self._new_id = new_id

    def change(
        self,
        field_ref: str,
        old_value: Any,
        new_value: Any,
        evidence: FieldEvidence,
        concern: RecognitionConcern | None = None,
    ) -> None:
        previous = self.provenance.get(field_ref)
        if (
            previous is not None
            and previous.source_kind == "user_reported"
            and evidence.source_kind != "user_reported"
        ):
            raise RecordConflict("Inferred information cannot replace a user-reported field")
        if concern is not None and new_value is None:
            raise RecordError("An absent value cannot carry a recognition concern")
        if concern is not None and evidence.source_kind != "image_recognized":
            raise RecordError("Recognition concerns require image-recognized evidence")
        same_evidence = (
            previous is not None
            and previous.model_dump(exclude={"field_ref", "value_revision"})
            == evidence.model_dump()
        )
        old_issues = [issue for issue in self.issues.values() if issue.field_ref == field_ref]
        same_concern = not old_issues
        if concern is not None:
            same_concern = (
                len(old_issues) == 1
                and old_issues[0].reason == concern.reason
                and old_issues[0].user_message == concern.user_message
            )
        if old_value == new_value and same_evidence and same_concern:
            return
        self.clear_issues(field_ref)
        self.provenance[field_ref] = FieldProvenance(
            **evidence.model_dump(), field_ref=field_ref, value_revision=self._revision
        )
        self.changed_fields.add(field_ref)
        self.source_refs.update(evidence.source_refs)
        if concern is not None:
            issue = RecognitionIssue(
                issue_id=self._new_id(),
                field_ref=field_ref,
                value_revision=self._revision,
                **concern.model_dump(),
            )
            if issue.issue_id in self.issues or issue.issue_id in self.cleared_issue_ids:
                raise RecordError("Generated recognition issue identity is not unique")
            self.issues[issue.issue_id] = issue

    def clear_issues(self, field_ref: str) -> None:
        for issue in tuple(self.issues.values()):
            if issue.field_ref == field_ref:
                self.cleared_issue_ids.add(issue.issue_id)
                del self.issues[issue.issue_id]

    def remove_item(self, item_id: str, evidence: FieldEvidence) -> None:
        if evidence.source_kind != "user_reported":
            raise RecordError("Removing a food requires user evidence")
        prefix = f"foods/{item_id}/"
        for field_ref in tuple(self.provenance):
            if field_ref.startswith(prefix):
                self.clear_issues(field_ref)
                del self.provenance[field_ref]
        self.changed_fields.add(f"foods/{item_id}")
        self.removed_item_ids.add(item_id)
        self.source_refs.update(evidence.source_refs)
