"""Remove obsolete budget columns without dropping invocation or training history."""

import json
from typing import cast

from sqlalchemy import Table, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from slim_guard.db.models import AgentInvocationRecord
from slim_guard.style_management.models import BuildRun
from slim_guard.usage.models import ModelUsageRecord


async def migrate_usage(connection: AsyncConnection) -> None:
    await connection.run_sync(cast(Table, ModelUsageRecord.__table__).create, checkfirst=True)
    columns = await connection.run_sync(lambda c: inspect(c).get_columns("agent_invocations"))
    if any(c["name"] == "max_total_tokens" for c in columns):
        if connection.dialect.name == "postgresql":
            for suffix in ("model", "tool", "token"):
                await connection.execute(
                    text(
                        "ALTER TABLE agent_invocations DROP CONSTRAINT IF EXISTS "
                        f"ck_agent_invocation_{suffix}_budget"
                    )
                )
            for name in ("max_model_calls", "max_tool_calls", "max_total_tokens"):
                await connection.execute(text(f"ALTER TABLE agent_invocations DROP COLUMN {name}"))
        elif connection.dialect.name == "sqlite":
            # Database's SQLite adapter uses foreign_keys=OFF. Rebuild in place so
            # references from other tables and all invocation IDs remain unchanged.
            if await connection.scalar(text("PRAGMA foreign_keys")):
                raise RuntimeError("Run SQLite budget migration with foreign_keys disabled")
            table = cast(Table, AgentInvocationRecord.__table__)
            ddl = str(CreateTable(table).compile(dialect=connection.dialect))
            ddl = ddl.replace(
                "CREATE TABLE agent_invocations", "CREATE TABLE agent_invocations_usage", 1
            )
            await connection.execute(text(ddl))
            names = ", ".join(c.name for c in table.columns)
            await connection.execute(
                text(
                    f"INSERT INTO agent_invocations_usage ({names}) "
                    f"SELECT {names} FROM agent_invocations"
                )
            )
            await connection.execute(text("DROP TABLE agent_invocations"))
            await connection.execute(
                text("ALTER TABLE agent_invocations_usage RENAME TO agent_invocations")
            )
            for index in table.indexes:
                await connection.run_sync(index.create)
        else:
            raise RuntimeError("Unsupported usage migration database")
    rows = (await connection.execute(select(BuildRun.id, BuildRun.snapshot))).all()
    for run_id, snapshot in rows:
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        if not isinstance(snapshot, dict):
            continue
        execution = dict(snapshot.get("execution") or snapshot.get("budget") or {})
        execution.pop("max_calls", None)
        execution.pop("max_tokens", None)
        new = {k: v for k, v in snapshot.items() if k != "budget"}
        new["execution"] = execution
        await connection.execute(update(BuildRun).where(BuildRun.id == run_id).values(snapshot=new))
