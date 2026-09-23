"""Bounded context projection. Quotes and model interpretations stay distinct."""

from typing import Any

from slim_guard.tasks.contracts import TaskRef


def task_summary(task: TaskRef) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "thread_id": task.thread_id,
        "revision": task.revision,
        "status": task.status,
        "objective": task.objective,
        "unresolved": list(task.unresolved),
        "evidence": [e.model_dump() for e in task.evidence[-20:]],
        "evidence_truncated": len(task.evidence) > 20,
        "source_item_ids": list(task.source_item_ids[-20:]),
        "attachment_ids": list(task.attachment_ids[-20:]),
        "expires_at": task.expires_at.isoformat(),
    }
