"""Explicit admin projections; never leak worker ownership or full job checkpoints."""

from typing import Any

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
    data["round_count"] = len(row.checkpoints.get("rounds", []))
    data["max_rounds"] = row.snapshot.get("budget", {}).get("max_rounds", 0)
    data["baseline_version"] = row.snapshot.get("baseline", {}).get("version_id")
    data["last_event_sequence"] = len(row.events)
    return data
