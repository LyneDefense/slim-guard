"""Dashboard projection of authoritative records; no shadow record storage."""

from typing import Any

from slim_guard.features.health_records.content import BodyFatContent, WeightContent
from slim_guard.features.health_records.queries import RecordQuery
from slim_guard.features.health_records.service import RecordService
from slim_guard.mobile.contracts import ProgressView, TrendPoint


async def record_progress(records: RecordService, user_id: str, limit: int) -> ProgressView:
    pages = {
        kind: await records.search(user_id, RecordQuery(kind=kind, limit=limit))
        for kind in ("weight", "body_fat", "meal", "exercise")
    }
    metrics = {}
    for kind in ("weight", "body_fat"):
        points = []
        for record in reversed(pages[kind].records):
            occurrence = record.occurrence.at or record.occurrence.on
            if occurrence is None:
                continue
            content = record.content
            if isinstance(content, WeightContent):
                value = content.weight_grams / 1000
            elif isinstance(content, BodyFatContent):
                value = content.basis_points / 100
            else:
                raise ValueError("Unexpected measurement content")
            points.append(
                TrendPoint(
                    id=record.ref.record_id,
                    value=value,
                    occurred_at=occurrence,
                    time_precision=record.occurrence.precision,
                )
            )
        metrics[kind] = points

    def summaries(kind: str) -> list[dict[str, Any]]:
        return [
            {
                "id": record.ref.record_id,
                "revision": record.ref.revision,
                **record.content.model_dump(mode="json", exclude={"kind"}),
                "occurred_at": record.occurrence.at,
                "occurred_on": record.occurrence.on,
                "time_precision": record.occurrence.precision,
                "recognition_issues": [issue.model_dump() for issue in record.recognition_issues],
            }
            for record in reversed(pages[kind].records)
        ]

    return ProgressView(
        weights=metrics["weight"],
        body_fat=metrics["body_fat"],
        meals=summaries("meal"),
        exercise=summaries("exercise"),
    )
