"""Add record versions without changing record identity or inventing provenance."""

import json
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Table, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from slim_guard.db.models import BodyFatRecord, ExerciseRecord, MealRecord, WeightRecord
from slim_guard.features.health_records.persistence.receipts import RecordMutationReceipt

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
    rows = (await connection.execute(text("SELECT id, foods_json FROM meal_records"))).mappings()
    for row in rows:
        foods = json.loads(row["foods_json"])
        changed = False
        for index, food in enumerate(foods):
            if not food.get("item_id"):
                food["item_id"] = str(uuid5(NAMESPACE_URL, f"slim-guard/meal/{row['id']}/{index}"))
                changed = True
        if changed:
            await connection.execute(
                text("UPDATE meal_records SET foods_json=:foods WHERE id=:id"),
                {"foods": json.dumps(foods, ensure_ascii=False), "id": row["id"]},
            )
