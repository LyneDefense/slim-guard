"""Add record versions while marking unavailable legacy evidence honestly."""

import json
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Table, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    BodyFatRecord,
    ExerciseRecord,
    MealRecord,
    WeightRecord,
)
from slim_guard.features.health_records.persistence.receipts import RecordMutationReceipt
from slim_guard.features.health_records.provenance import FieldProvenance

TABLES = (
    cast(Table, MealRecord.__table__),
    cast(Table, ExerciseRecord.__table__),
    cast(Table, WeightRecord.__table__),
    cast(Table, BodyFatRecord.__table__),
)


def _columns(connection: Connection, name: str) -> Any:
    return inspect(connection).get_columns(name)


def _indexes(connection: Connection, name: str) -> Any:
    return inspect(connection).get_indexes(name)


def _legacy_field_refs(table_name: str, row: Any) -> tuple[str, ...]:
    """Return the fields represented by an old row, without asserting who supplied them."""

    refs = ["occurrence", "status"]
    if table_name == "meal_records":
        refs.extend(("meal_type", "foods/order", "note"))
        refs.extend(
            field_ref
            for food in json.loads(row["foods_json"])
            for field_ref in (
                f"foods/{food['item_id']}/name",
                f"foods/{food['item_id']}/portion",
            )
        )
    elif table_name == "exercise_records":
        refs.extend(
            (
                "activity_name",
                "duration_minutes",
                "steps",
                "distance_meters",
                "reported_energy_kcal",
                "note",
            )
        )
    elif table_name == "weight_records":
        refs.extend(("measurement", "condition"))
    else:
        refs.append("measurement")
    return tuple(refs)


def _legacy_provenance(table_name: str, row: Any) -> str:
    explanation = "Migrated legacy value; original field-level evidence is unavailable."
    entries = [
        FieldProvenance(
            field_ref=field_ref,
            value_revision=1,
            source_kind="legacy",
            explanation=explanation,
        ).model_dump()
        for field_ref in _legacy_field_refs(table_name, row)
    ]
    return json.dumps(entries, ensure_ascii=False)


async def migrate_health_records(connection: AsyncConnection) -> None:
    task_columns = await connection.run_sync(
        lambda sync: inspect(sync).get_columns("conversation_tasks")
    )
    if not any(column["name"] == "request_json" for column in task_columns):
        await connection.execute(
            text("ALTER TABLE conversation_tasks ADD COLUMN request_json TEXT")
        )
    await connection.run_sync(cast(Table, RecordMutationReceipt.__table__).create, checkfirst=True)
    for table in TABLES:
        columns = await connection.run_sync(_columns, table.name)
        names = {column["name"] for column in columns}
        added_precision = "time_precision" not in names
        added_provenance = "field_provenance_json" not in names
        additions = {
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "TIMESTAMP",
            "occurred_on": "DATE",
            "time_precision": "VARCHAR(16) NOT NULL DEFAULT 'datetime'",
            "timezone": "VARCHAR(64) NOT NULL DEFAULT 'UTC'",
            "time_basis": "VARCHAR(32) NOT NULL DEFAULT 'legacy'",
            "source_refs_json": "TEXT NOT NULL DEFAULT '[]'",
            "field_provenance_json": "TEXT NOT NULL DEFAULT '[]'",
            "recognition_issues_json": "TEXT NOT NULL DEFAULT '[]'",
            "retired_reason": "VARCHAR(128)",
        }
        for name, ddl in additions.items():
            if name not in names:
                await connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {name} {ddl}"))
        await connection.execute(
            text(f"UPDATE {table.name} SET updated_at=created_at WHERE updated_at IS NULL")
        )
        await connection.execute(
            text(
                f"UPDATE {table.name} SET status='voided', retired_reason='legacy_superseded' "
                "WHERE status='superseded'"
            )
        )
        time_column = (
            "measured_at" if table.name in {"weight_records", "body_fat_records"} else "occurred_at"
        )
        old_time = next(column for column in columns if column["name"] == time_column)
        if connection.dialect.name == "postgresql":
            await connection.execute(
                text(f"ALTER TABLE {table.name} ALTER COLUMN {time_column} DROP NOT NULL")
            )
            await connection.execute(
                text(f"ALTER TABLE {table.name} ALTER COLUMN updated_at SET NOT NULL")
            )
        elif not old_time["nullable"]:
            # Only weight has an inbound reference, from itself. Keep original IDs
            # and recreate the self reference against the new table, not its backup.
            backup = table.name + "_before_record_versions"
            await connection.execute(text(f"ALTER TABLE {table.name} RENAME TO {backup}"))
            for old_index in await connection.run_sync(_indexes, backup):
                await connection.execute(text(f'DROP INDEX "{old_index["name"]}"'))
            await connection.execute(CreateTable(table))
            selected = ",".join(column.name for column in table.columns)
            await connection.execute(
                text(f"INSERT INTO {table.name} ({selected}) SELECT {selected} FROM {backup}")
            )
            await connection.execute(text(f"DROP TABLE {backup}"))
            for new_index in table.indexes:
                await connection.run_sync(new_index.create, checkfirst=True)
        if added_precision:
            await connection.execute(
                text(
                    f"UPDATE {table.name} SET "
                    "time_precision=CASE "
                    f"WHEN {time_column} IS NOT NULL THEN 'datetime' "
                    "WHEN occurred_on IS NOT NULL THEN 'date' ELSE 'unknown' END, "
                    "time_basis=CASE "
                    f"WHEN {time_column} IS NOT NULL OR occurred_on IS NOT NULL "
                    "THEN 'legacy' ELSE 'unknown' END"
                )
            )
        if table.name == "meal_records":
            rows = (
                await connection.execute(text("SELECT id, foods_json FROM meal_records"))
            ).mappings()
            for row in rows:
                foods = json.loads(row["foods_json"])
                changed = False
                for index, food in enumerate(foods):
                    if not food.get("item_id"):
                        food["item_id"] = str(
                            uuid5(NAMESPACE_URL, f"slim-guard/meal/{row['id']}/{index}")
                        )
                        changed = True
                if changed:
                    await connection.execute(
                        text("UPDATE meal_records SET foods_json=:foods WHERE id=:id"),
                        {"foods": json.dumps(foods, ensure_ascii=False), "id": row["id"]},
                    )
        if added_provenance or "source_refs_json" not in names:
            rows = (await connection.execute(text(f"SELECT * FROM {table.name}"))).mappings()
            for row in rows:
                updates: dict[str, str] = {}
                if added_provenance and not json.loads(row["field_provenance_json"]):
                    updates["field_provenance_json"] = _legacy_provenance(table.name, row)
                source_item_id = row.get("source_item_id")
                if "source_refs_json" not in names and source_item_id:
                    source_user_id = await connection.scalar(
                        select(AgentThreadRecord.user_id)
                        .join(
                            AgentItemRecord,
                            AgentItemRecord.thread_id == AgentThreadRecord.id,
                        )
                        .where(
                            AgentItemRecord.id == source_item_id,
                            AgentItemRecord.turn_id == row["source_turn_id"],
                        )
                    )
                    if source_user_id == row["user_id"]:
                        updates["source_refs_json"] = json.dumps([source_item_id])
                if updates:
                    assignments = ", ".join(f"{name} = :{name}" for name in updates)
                    await connection.execute(
                        text(f"UPDATE {table.name} SET {assignments} WHERE id=:id"),
                        {**updates, "id": row["id"]},
                    )
