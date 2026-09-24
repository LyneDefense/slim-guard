"""Operation snapshots follow transcript retention and user-data deletion."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentTurnRecord
from slim_guard.tool_operations.models import ToolOperationRecord
from slim_guard.tools.contracts import ToolResult


async def scrub_operation_bodies(
    session: AsyncSession,
    *,
    before: datetime,
    limit: int,
) -> int:
    rows = tuple(
        await session.scalars(
            select(ToolOperationRecord)
            .join(
                AgentTurnRecord,
                AgentTurnRecord.id == ToolOperationRecord.source_turn_id,
            )
            .where(
                ToolOperationRecord.updated_at <= before,
                AgentTurnRecord.status.in_(("completed", "failed", "suspended")),
                ~ToolOperationRecord.arguments_json.like('%"_redacted":true%'),
            )
            .limit(limit)
        )
    )
    for row in rows:
        row.arguments_json = json.dumps(
            {
                "_redacted": True,
                "_redacted_sha256": hashlib.sha256(row.arguments_json.encode()).hexdigest(),
            },
            separators=(",", ":"),
        )
        if row.result_json is not None:
            result = ToolResult.model_validate_json(row.result_json)
            failure = result.failure
            if failure is not None:
                failure = failure.model_copy(
                    update={
                        "message": "Operation detail removed by transcript retention.",
                        "field_errors": (),
                    }
                )
            row.result_json = result.model_copy(
                update={
                    "output": {"_redacted": True},
                    "failure": failure,
                }
            ).to_model_content()
    # Preserve identity and the uncertainty guard. Retention must not authorize a retry.
    return len(rows)


async def delete_user_operations(session: AsyncSession, user_id: str) -> None:
    await session.execute(delete(ToolOperationRecord).where(ToolOperationRecord.user_id == user_id))
