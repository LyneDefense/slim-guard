"""Persist actual calls and aggregate them once, never parent invocation totals."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import InteractionTraceRecord
from slim_guard.db.session import Database
from slim_guard.usage.exchanges import exchange_index
from slim_guard.usage.invocations import execution_counts
from slim_guard.usage.models import ModelUsageRecord as Call


def dated_row(values: Mapping[str, Any]) -> dict[str, Any]:
    # SQLite drops tzinfo; the ledger writes UTC, not server-local timestamps.
    return {
        key: value.replace(tzinfo=UTC)
        if isinstance(value, datetime) and value.tzinfo is None
        else value
        for key, value in values.items()
    }


def totals() -> tuple[Any, ...]:
    return (
        func.count(Call.id).label("calls"),
        func.count(
            func.distinct(func.coalesce(Call.turn_id, Call.request_id, Call.job_id, Call.id))
        ).label("exchanges"),
        func.count(func.distinct(Call.invocation_id)).label("invocations"),
        func.coalesce(func.sum(Call.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(Call.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(Call.total_tokens), 0).label("total_tokens"),
        func.sum(case((Call.total_tokens.is_(None), 1), else_=0)).label("unknown_calls"),
        func.sum(case((Call.status == "failed", 1), else_=0)).label("failed_calls"),
        func.sum(case((Call.status == "running", 1), else_=0)).label("running_calls"),
    )


class UsageRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def start(self, values: dict[str, Any]) -> None:
        async with self.db.session() as s, s.begin():
            s.add(Call(**values))

    async def finish(self, call_id: str, values: dict[str, Any]) -> None:
        async with self.db.session() as s, s.begin():
            await s.execute(update(Call).where(Call.id == call_id).values(**values))

    @staticmethod
    async def delete_user(session: AsyncSession, user_id: str) -> None:
        await session.execute(delete(Call).where(Call.user_id == user_id))

    @staticmethod
    async def reassign_user(session: AsyncSession, source_id: str, target_id: str) -> None:
        await session.execute(
            update(Call).where(Call.user_id == source_id).values(user_id=target_id)
        )

    async def users(
        self, *, start: datetime | None, end: datetime | None, limit: int, offset: int
    ) -> dict[str, Any]:
        filters = self._dates(start, end) + [Call.user_id.is_not(None)]
        stmt = select(Call.user_id, *totals()).where(*filters).group_by(Call.user_id)
        async with self.db.session() as s:
            count = await s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await s.execute(
                        stmt.order_by(func.max(Call.started_at).desc(), Call.user_id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
        return {
            "items": [dated_row(dict(r)) for r in rows],
            "total": count,
            "limit": limit,
            "offset": offset,
        }

    async def requests(
        self,
        *,
        user_id: str | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        # Turn is stable across online and asynchronous calls. Failed pre-Turn calls use request ID.
        key = func.coalesce(Call.turn_id, Call.request_id, Call.job_id, Call.id)
        index = exchange_index()
        aggregated = (
            select(key.label("id"), *totals())
            .where(Call.user_id == user_id)
            .group_by(key)
            .subquery()
        )
        stmt = (
            select(
                index.c.id,
                index.c.started_at,
                index.c.status,
                index.c.category,
                *(
                    func.coalesce(aggregated.c[name], 0).label(name)
                    for name in (
                        "calls",
                        "invocations",
                        "input_tokens",
                        "output_tokens",
                        "total_tokens",
                        "unknown_calls",
                        "failed_calls",
                        "running_calls",
                    )
                ),
            )
            .outerjoin(aggregated, aggregated.c.id == index.c.id)
            .where(
                index.c.user_id == user_id,
                *([index.c.started_at >= start] if start else []),
                *([index.c.started_at < end] if end else []),
            )
        )
        async with self.db.session() as s:
            count = await s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (
                (
                    await s.execute(
                        stmt.order_by(index.c.started_at.desc(), index.c.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
        return {
            "items": [dated_row(dict(r)) for r in rows],
            "total": count,
            "limit": limit,
            "offset": offset,
        }

    async def detail(
        self, *, user_id: str | None, request_id: str, limit: int, offset: int
    ) -> dict[str, Any]:
        key = func.coalesce(Call.turn_id, Call.request_id, Call.job_id, Call.id)
        filters = [Call.user_id == user_id, key == request_id]
        async with self.db.session() as s:
            summary = (await s.execute(select(*totals()).where(*filters))).mappings().one()
            trace_id = (
                await s.scalar(
                    select(InteractionTraceRecord.id)
                    .where(
                        InteractionTraceRecord.user_id == user_id,
                        InteractionTraceRecord.agent_turn_id == request_id,
                    )
                    .limit(1)
                )
                if user_id
                else None
            )
            agents = (
                (
                    await s.execute(
                        select(Call.agent_role, *totals())
                        .where(*filters)
                        .group_by(Call.agent_role)
                        .order_by(Call.agent_role)
                    )
                )
                .mappings()
                .all()
            )
            groups = (
                (
                    await s.execute(
                        select(
                            Call.agent_role,
                            Call.execution_kind,
                            Call.component,
                            *totals(),
                        )
                        .where(*filters)
                        .group_by(Call.agent_role, Call.execution_kind, Call.component)
                        .order_by(Call.agent_role, Call.component)
                    )
                )
                .mappings()
                .all()
            )
            rows = list(
                await s.scalars(
                    select(Call)
                    .where(*filters)
                    .order_by(Call.started_at, Call.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            executions = await execution_counts(
                s, user_id=user_id, exchange_id=request_id, call_filters=filters
            )
        agent_totals = {row["agent_role"]: dict(row) for row in agents}
        for role, count in executions.items():
            if role not in agent_totals:
                agent_totals[role] = {**{key: 0 for key in summary}, "agent_role": role}
            agent_totals[role]["executions"] = count
        return {
            "summary": dict(summary),
            "executions": sum(executions.values()),
            "trace_id": trace_id,
            "groups": [dict(r) for r in groups],
            "agents": list(agent_totals.values()),
            "items": [
                dated_row({c.name: getattr(r, c.name) for c in Call.__table__.columns})
                for r in rows
            ],
            "total": summary["calls"],
            "limit": limit,
            "offset": offset,
            "coverage": "仅统计已接入的模型请求；外部 Mem0 服务内部消耗未覆盖。未知用量不按零计。",
        }

    @staticmethod
    def _dates(start: datetime | None, end: datetime | None) -> list[Any]:
        return ([Call.started_at >= start] if start else []) + (
            [Call.started_at < end] if end else []
        )
