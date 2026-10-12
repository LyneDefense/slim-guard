import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from slim_guard.db.models import BodyFatRecord, ExerciseRecord, MealRecord, WeightRecord
from slim_guard.features.health_records.content import (
    BodyFatContent,
    ExerciseContent,
    MealContent,
    WeightContent,
)
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.views import RecordContent, RecordView

RECORD_MODELS: dict[str, Any] = {
    "meal": MealRecord,
    "exercise": ExerciseRecord,
    "weight": WeightRecord,
    "body_fat": BodyFatRecord,
}


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_view(kind: str, row: Any) -> RecordView:
    content: RecordContent
    if kind == "meal":
        foods = json.loads(row.foods_json)
        for index, food in enumerate(foods):
            if not food.get("item_id"):
                food["item_id"] = str(uuid5(NAMESPACE_URL, f"slim-guard/meal/{row.id}/{index}"))
        content = MealContent(meal_type=row.meal_type, foods=foods, note=row.note)
    elif kind == "exercise":
        content = ExerciseContent(
            **{key: getattr(row, key) for key in ExerciseContent.model_fields if key != "kind"}
        )
    elif kind == "weight":
        content = WeightContent(
            weight_grams=row.weight_grams,
            original_value=row.original_value,
            original_unit=row.original_unit,
            condition=row.measurement_condition,
        )
    else:
        content = BodyFatContent(
            basis_points=row.body_fat_basis_points, original_value=row.original_value
        )
    instant = getattr(row, "occurred_at", None) if kind in {"meal", "exercise"} else row.measured_at
    occurrence = Occurrence(
        precision=row.time_precision,
        at=aware(instant) if instant else None,
        on=row.occurred_on,
        timezone=row.timezone,
        basis=row.time_basis,
    )
    provenance = json.loads(row.field_provenance_json)
    versions = {entry["field_ref"]: entry["value_revision"] for entry in provenance}
    issues = [
        issue
        for issue in json.loads(row.recognition_issues_json)
        if versions.get(issue["field_ref"]) == issue["value_revision"]
    ]
    return RecordView.model_validate(
        {
            "ref": {"kind": kind, "record_id": row.id, "revision": row.revision},
            "status": row.status,
            "occurrence": occurrence,
            "recorded_at": aware(row.created_at),
            "updated_at": aware(row.updated_at),
            "content": content,
            "source_refs": json.loads(row.source_refs_json),
            "field_provenance": provenance,
            "recognition_issues": issues,
        }
    )


def row_values(record: RecordView) -> dict[str, Any]:
    content = record.content
    values: dict[str, Any] = {
        "revision": record.ref.revision,
        "status": record.status,
        "updated_at": record.updated_at,
        "occurred_on": record.occurrence.on,
        "time_precision": record.occurrence.precision,
        "timezone": record.occurrence.timezone,
        "time_basis": record.occurrence.basis,
        "source_refs_json": json.dumps(record.source_refs),
        "field_provenance_json": json.dumps([p.model_dump() for p in record.field_provenance]),
        "recognition_issues_json": json.dumps([p.model_dump() for p in record.recognition_issues]),
    }
    values["occurred_at" if content.kind in {"meal", "exercise"} else "measured_at"] = (
        record.occurrence.at
    )
    if isinstance(content, MealContent):
        values.update(
            meal_type=content.meal_type,
            foods_json=json.dumps(
                [food.model_dump() for food in content.foods], ensure_ascii=False
            ),
            note=content.note,
        )
    elif isinstance(content, ExerciseContent):
        values.update(content.model_dump(exclude={"kind"}))
    elif isinstance(content, WeightContent):
        values.update(
            weight_grams=content.weight_grams,
            original_value=str(content.original_value),
            original_unit=content.original_unit,
            measurement_condition=content.condition,
        )
    else:
        values.update(
            body_fat_basis_points=content.basis_points, original_value=str(content.original_value)
        )
    return values
