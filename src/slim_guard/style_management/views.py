"""Explicit admin projections; never leak worker ownership or full job checkpoints."""

from typing import Any

from slim_guard.expression_style.trainer.contracts import WORKFLOW_VERSION

from .models import BuildRun


def row_data(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def build_summary(row: BuildRun) -> dict[str, Any]:
    data = row_data(row)
    for key in ("worker_token", "lease_until", "snapshot", "checkpoints", "events"):
        data.pop(key, None)
    data["material_count"] = len(row.snapshot.get("materials", []))
    data["library_count"] = row.snapshot.get("library_count", data["material_count"])
    data["human_feedback_count"] = row.snapshot.get("human_feedback_count", 0)
    data["unused_count"] = row.snapshot.get("unused_count", 0)
    data["analyzed_count"] = len(row.checkpoints.get("materials", []))
    data["workflow_version"] = row.snapshot.get("workflow_version", "legacy")
    data["job_kind"] = row.snapshot.get("job_kind", "build")
    data["target_version_id"] = row.snapshot.get("retry_version_id")
    data["can_resume"] = data["workflow_version"] == WORKFLOW_VERSION and (
        row.status != "failed" or row.activity.get("retryable") is not False
    )
    data["resume_block_reason"] = (
        "workflow_changed"
        if data["workflow_version"] != WORKFLOW_VERSION
        else "not_retryable"
        if not data["can_resume"]
        else None
    )
    data["preview_count"] = (
        len(row.snapshot.get("retry_cases", []))
        if data["job_kind"] == "preview_retry"
        else len(row.checkpoints.get("preview_suite", {}).get("cases", []))
    )
    data["baseline_version"] = row.snapshot.get("baseline", {}).get("version_id")
    data["last_event_sequence"] = len(row.events)
    return data
