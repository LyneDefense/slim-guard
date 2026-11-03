"""Model snapshots share transcript retention; accounting and causal IDs survive."""

import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import TraceSpanRecord


async def scrub_model_exchanges(session: AsyncSession, *, before: datetime, limit: int) -> int:
    rows = await session.scalars(
        select(TraceSpanRecord)
        .where(
            TraceSpanRecord.operation == "model_exchange",
            TraceSpanRecord.started_at <= before,
            or_(TraceSpanRecord.completed_at <= before, TraceSpanRecord.completed_at.is_(None)),
            ~TraceSpanRecord.attributes_json.like('%"body_redacted":true%'),
        )
        .order_by(TraceSpanRecord.completed_at, TraceSpanRecord.id)
        .limit(limit)
    )
    count = 0
    for row in rows:
        data = json.loads(row.attributes_json)
        data.pop("input", None)
        data.pop("output", None)
        data["body_redacted"] = True
        row.attributes_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        count += 1
    return count
