"""Bounded-page read adapter for the call-monitor feature. No business mutations."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, or_, select

from slim_guard.admin.calls.projection import instant, mapping, project, summary
from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.db.models import (
    AgentInvocationRecord,
    AgentItemRecord,
    AgentItemRedactionRecord,
    InteractionTraceRecord,
    MemoryExtractionJobRecord,
    SlimGuardUser,
    ToolExecutionRecord,
)
from slim_guard.db.session import Database
from slim_guard.usage.repository import UsageRepository


def load(value: str) -> dict[str, Any]:
    try:
        return mapping(json.loads(value))
    except (ValueError, TypeError):
        return {}


class CallMonitorRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_calls(
        self,
        *,
        search: str,
        user_id: str | None,
        result: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        trace = InteractionTraceRecord
        conditions: list[Any] = []
        if user_id:
            conditions.append(trace.user_id == user_id)
        if start:
            conditions.append(trace.created_at >= start)
        if end:
            conditions.append(trace.created_at < end)
        if result == "running":
            conditions.append(trace.generation_status.in_(("running", "pending")))
        if result == "issues":
            conditions.append(
                or_(
                    trace.generation_status.in_(("failed", "degraded")),
                    trace.delivery_status.in_(("failed", "unknown")),
                    trace.agent_turn_id.in_(UsageRepository.failed_turn_ids_query()),
                    exists(
                        select(AgentItemRecord.id).where(
                            AgentItemRecord.turn_id == trace.agent_turn_id,
                            AgentItemRecord.item_type == "structured_output_rejected",
                        )
                    ),
                    exists(
                        select(AgentInvocationRecord.id).where(
                            AgentInvocationRecord.trace_id == trace.id,
                            or_(
                                AgentInvocationRecord.attempt > 1,
                                AgentInvocationRecord.status == "failed",
                            ),
                        )
                    ),
                )
            )
        if search:
            pattern = (
                "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            conditions.append(
                or_(
                    trace.id.ilike(pattern, escape="\\"),
                    trace.user_id.ilike(pattern, escape="\\"),
                    SlimGuardUser.nickname.ilike(pattern, escape="\\"),
                    exists(
                        select(AgentItemRecord.id).where(
                            AgentItemRecord.turn_id == trace.agent_turn_id,
                            AgentItemRecord.item_type == "user_message",
                            AgentItemRecord.payload_json.ilike(pattern, escape="\\"),
                            ~exists(
                                select(AgentItemRedactionRecord.item_id).where(
                                    AgentItemRedactionRecord.item_id == AgentItemRecord.id
                                )
                            ),
                        )
                    ),
                )
            )
        base = (
            select(trace, SlimGuardUser.nickname)
            .join(SlimGuardUser, SlimGuardUser.id == trace.user_id)
            .where(*conditions)
        )
        async with self._db.session() as session:
            total = int(
                await session.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await session.execute(
                    base.order_by(trace.created_at.desc(), trace.id).offset(offset).limit(limit)
                )
            ).all()
            turns = [t.agent_turn_id for t, _ in rows if t.agent_turn_id]
            items = (
                (
                    await session.execute(
                        select(AgentItemRecord, AgentItemRedactionRecord)
                        .outerjoin(
                            AgentItemRedactionRecord,
                            AgentItemRedactionRecord.item_id == AgentItemRecord.id,
                        )
                        .where(
                            AgentItemRecord.turn_id.in_(turns),
                            AgentItemRecord.item_type.in_(
                                (
                                    "user_message",
                                    "image_attachment",
                                    "agent_message",
                                    "tool_result",
                                    "structured_output_rejected",
                                )
                            ),
                        )
                        .order_by(AgentItemRecord.sequence)
                    )
                ).all()
                if turns
                else []
            )
            tools = (
                list(
                    await session.scalars(
                        select(ToolExecutionRecord).where(ToolExecutionRecord.turn_id.in_(turns))
                    )
                )
                if turns
                else []
            )
            invocations = (
                list(
                    await session.scalars(
                        select(AgentInvocationRecord).where(
                            AgentInvocationRecord.turn_id.in_(turns)
                        )
                    )
                )
                if turns
                else []
            )
        calls = await UsageRepository(self._db).turn_calls(turn_ids=turns, user_id=user_id)
        views = []
        for record, nickname in rows:
            turn_items = [(i, r) for i, r in items if i.turn_id == record.agent_turn_id]
            events = [
                {
                    "operation": i.item_type,
                    "details": load(i.payload_json) if r is None else {"redacted": True},
                }
                for i, r in turn_items
            ]
            messages = [
                mapping(e["details"]).get("text")
                for e in events
                if e["operation"] == "user_message"
            ]
            trace_view = {c.name: getattr(record, c.name) for c in trace.__table__.columns}
            for key in ("created_at", "completed_at"):
                trace_view[key] = instant(trace_view.get(key))
            trace_view["duration_ms"] = None
            if record.completed_at:
                trace_view["duration_ms"] = max(
                    0, int((record.completed_at - record.created_at).total_seconds() * 1000)
                )
            data = {
                "trace": trace_view,
                "timeline": events,
                "tool_executions": [
                    t.tool_call_id for t in tools if t.turn_id == record.agent_turn_id
                ],
                "invocations": [
                    {"attempt": i.attempt, "model_call_count": i.model_call_count}
                    for i in invocations
                    if i.turn_id == record.agent_turn_id
                ],
            }
            views.append(
                {
                    **trace_view,
                    "nickname": nickname,
                    "input_text": "\n".join(m for m in messages if isinstance(m, str)) or None,
                    "image_count": sum(
                        i.item_type == "image_attachment" and r is None for i, r in turn_items
                    ),
                    "process": summary(
                        data, [c for c in calls if c.get("turn_id") == record.agent_turn_id]
                    ),
                }
            )
        return {"items": views, "total": total, "limit": limit, "offset": offset}

    async def detail(self, *, user_id: str, trace_id: str) -> dict[str, Any] | None:
        data = await AdminQueryRepository(self._db).get_trace(user_id=user_id, trace_id=trace_id)
        if data is None:
            return None
        for key in ("created_at", "completed_at"):
            data["trace"][key] = instant(data["trace"].get(key))
        turn_id = data["trace"].get("agent_turn_id")
        calls = await UsageRepository(self._db).turn_calls(
            turn_ids=[turn_id] if turn_id else [], user_id=user_id
        )
        async with self._db.session() as session:
            rows = await session.scalars(
                select(MemoryExtractionJobRecord).where(
                    MemoryExtractionJobRecord.user_id == user_id,
                    MemoryExtractionJobRecord.turn_id == turn_id,
                )
            )
            jobs = [
                {
                    "id": j.id,
                    "kind": "记忆提取",
                    "status": j.status,
                    "attempt_count": j.attempt_count,
                    "error_code": j.error_code,
                    "created_at": j.created_at,
                    "completed_at": j.completed_at,
                }
                for j in rows
            ]
        return {**data, "process": project(data, calls, jobs)}
