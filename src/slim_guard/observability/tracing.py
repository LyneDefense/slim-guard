from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    ChannelIdentity,
    InteractionTraceRecord,
    OutboundMessage,
    ProactiveMessageRecord,
    RoutineJobRecord,
    TraceSpanRecord,
)
from slim_guard.db.session import Database

_current_trace_id: ContextVar[str | None] = ContextVar("slim_guard_trace_id", default=None)


def current_trace_id() -> str | None:
    return _current_trace_id.get()


@contextmanager
def bind_trace(trace_id: str | None) -> Iterator[None]:
    token = _current_trace_id.set(trace_id)
    try:
        yield
    finally:
        _current_trace_id.reset(token)


@dataclass(frozen=True, slots=True)
class TraceSpanRef:
    id: str
    trace_id: str
    started_at: datetime


class InteractionTraceRepository:
    """Persists correlation and timed component spans for user-visible output chains."""

    _TERMINAL_DELIVERY = frozenset(
        {"accepted", "failed", "unknown", "deferred_external_session", "skipped"}
    )

    def __init__(self, database: Database) -> None:
        self._database = database

    async def start_user_trace(
        self,
        *,
        user_id: str,
        trigger_type: str,
        channel_id: str,
        inbound_msgid: str,
    ) -> str:
        """Create a channel-neutral trace for non-WeCom user interactions."""

        row = InteractionTraceRecord(
            user_id=user_id,
            trigger_type=trigger_type,
            channel_id=channel_id,
            inbound_msgid=inbound_msgid,
            generation_status="running",
            delivery_status="planned",
        )
        async with self._database.session() as session, session.begin():
            session.add(row)
            await session.flush()
        return row.id

    async def backfill_existing(self, *, limit: int = 5000) -> int:
        """Best-effort correlation for messages created before Trace support."""

        created = 0
        async with self._database.session() as session, session.begin():
            outbound_rows = await session.execute(
                select(OutboundMessage, ChannelIdentity.user_id)
                .join(
                    ChannelIdentity,
                    (ChannelIdentity.channel_id == OutboundMessage.channel_id)
                    & (
                        ChannelIdentity.external_userid
                        == OutboundMessage.external_userid
                    ),
                )
                .outerjoin(
                    InteractionTraceRecord,
                    InteractionTraceRecord.outbound_idempotency_key
                    == OutboundMessage.idempotency_key,
                )
                .where(InteractionTraceRecord.id.is_(None))
                .order_by(OutboundMessage.created_at)
                .limit(limit)
            )
            for outbound, user_id in outbound_rows:
                turn = await self._find_historical_turn(
                    session=session,
                    user_id=user_id,
                    channel_id=outbound.channel_id,
                    source_message_id=outbound.inbound_msgid,
                )
                trace_id = self._new_trace_id()
                session.add(
                    InteractionTraceRecord(
                        id=trace_id,
                        user_id=user_id,
                        trigger_type="user_message",
                        channel_id=outbound.channel_id,
                        inbound_msgid=outbound.inbound_msgid,
                        outbound_idempotency_key=outbound.idempotency_key,
                        agent_turn_id=turn.id if turn is not None else None,
                        agent_version_id=(
                            turn.agent_version_id if turn is not None else None
                        ),
                        reply_kind="agent" if turn is not None else "frozen",
                        generation_status=(
                            "succeeded"
                            if turn is not None and turn.status == "completed"
                            else "unknown"
                        ),
                        delivery_status=outbound.status,
                        failure_code=(
                            None if turn is not None else "historical_trace_unlinked"
                        ),
                        created_at=outbound.created_at,
                        completed_at=outbound.completed_at,
                    )
                )
                session.add(
                    TraceSpanRecord(
                        trace_id=trace_id,
                        sequence=1,
                        component="migration",
                        operation="historical_trace_backfilled",
                        status="completed" if turn is not None else "unknown",
                        attributes_json=self._json(
                            {
                                "correlation": (
                                    "source_message_id" if turn is not None else "unlinked"
                                )
                            }
                        ),
                        started_at=outbound.created_at,
                        completed_at=outbound.created_at,
                    )
                )
                created += 1

            remaining = max(0, limit - created)
            if remaining:
                proactive_rows = await session.execute(
                    select(ProactiveMessageRecord, RoutineJobRecord.user_id, AgentTurnRecord)
                    .join(RoutineJobRecord, RoutineJobRecord.id == ProactiveMessageRecord.job_id)
                    .join(
                        AgentTurnRecord,
                        AgentTurnRecord.id == ProactiveMessageRecord.source_turn_id,
                    )
                    .outerjoin(
                        InteractionTraceRecord,
                        InteractionTraceRecord.routine_job_id == ProactiveMessageRecord.job_id,
                    )
                    .where(InteractionTraceRecord.id.is_(None))
                    .order_by(ProactiveMessageRecord.created_at)
                    .limit(remaining)
                )
                for proactive, user_id, turn in proactive_rows:
                    trace_id = self._new_trace_id()
                    session.add(
                        InteractionTraceRecord(
                            id=trace_id,
                            user_id=user_id,
                            trigger_type=turn.trigger_type,
                            channel_id=proactive.channel_id,
                            routine_job_id=proactive.job_id,
                            agent_turn_id=turn.id,
                            agent_version_id=turn.agent_version_id,
                            reply_kind="proactive",
                            generation_status=(
                                "succeeded" if turn.status == "completed" else "unknown"
                            ),
                            delivery_status=proactive.status,
                            created_at=proactive.created_at,
                            completed_at=proactive.completed_at,
                        )
                    )
                    session.add(
                        TraceSpanRecord(
                            trace_id=trace_id,
                            sequence=1,
                            component="migration",
                            operation="historical_trace_backfilled",
                            status="completed",
                            attributes_json=self._json(
                                {"correlation": "proactive_source_turn_id"}
                            ),
                            started_at=proactive.created_at,
                            completed_at=proactive.created_at,
                        )
                    )
                    created += 1
        return created

    async def ensure_routine_trace(
        self,
        *,
        user_id: str,
        routine_job_id: str,
        trigger_type: str,
        channel_id: str | None = None,
    ) -> str:
        async with self._database.session() as session:
            existing = await session.scalar(
                select(InteractionTraceRecord.id).where(
                    InteractionTraceRecord.routine_job_id == routine_job_id
                )
            )
            if existing is not None:
                return existing
            row = InteractionTraceRecord(
                user_id=user_id,
                routine_job_id=routine_job_id,
                trigger_type=trigger_type,
                channel_id=channel_id,
                generation_status="pending",
                delivery_status="planned",
            )
            session.add(row)
            try:
                await session.commit()
                return row.id
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(InteractionTraceRecord.id).where(
                        InteractionTraceRecord.routine_job_id == routine_job_id
                    )
                )
                if existing is None:
                    raise
                return cast(str, existing)

    async def find_by_outbound(self, outbound_idempotency_key: str) -> str | None:
        async with self._database.session() as session:
            return cast(
                str | None,
                await session.scalar(
                    select(InteractionTraceRecord.id).where(
                        InteractionTraceRecord.outbound_idempotency_key
                        == outbound_idempotency_key
                    )
                ),
            )

    async def attach_agent_turn(
        self,
        *,
        trace_id: str,
        turn_id: str,
        agent_version_id: str,
    ) -> None:
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(InteractionTraceRecord)
                .where(InteractionTraceRecord.id == trace_id)
                .values(
                    agent_turn_id=turn_id,
                    agent_version_id=agent_version_id,
                    updated_at=datetime.now(UTC),
                )
            )

    async def mark_generation(
        self,
        *,
        trace_id: str,
        status: str,
        reply_kind: str | None = None,
        failure_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "generation_status": status,
            "updated_at": datetime.now(UTC),
        }
        if reply_kind is not None:
            values["reply_kind"] = reply_kind
        if failure_code is not None:
            values["failure_code"] = failure_code[:128]
        if error_detail is not None:
            values["error_detail"] = error_detail[:1024]
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(InteractionTraceRecord)
                .where(InteractionTraceRecord.id == trace_id)
                .values(**values)
            )

    async def mark_generation_succeeded_if_running(
        self,
        *,
        trace_id: str,
        reply_kind: str = "agent",
    ) -> None:
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(InteractionTraceRecord)
                .where(
                    InteractionTraceRecord.id == trace_id,
                    InteractionTraceRecord.generation_status == "running",
                )
                .values(
                    generation_status="succeeded",
                    reply_kind=reply_kind,
                    updated_at=datetime.now(UTC),
                )
            )

    async def mark_generation_unknown_if_pending(self, *, trace_id: str) -> None:
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(InteractionTraceRecord)
                .where(
                    InteractionTraceRecord.id == trace_id,
                    InteractionTraceRecord.generation_status == "pending",
                )
                .values(
                    generation_status="unknown",
                    reply_kind="frozen",
                    failure_code="generation_state_not_observed",
                    updated_at=datetime.now(UTC),
                )
            )

    async def mark_delivery(
        self,
        *,
        trace_id: str,
        status: str,
        failure_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        values: dict[str, Any] = {"delivery_status": status, "updated_at": now}
        if status in self._TERMINAL_DELIVERY:
            values["completed_at"] = now
        if failure_code is not None:
            values["failure_code"] = failure_code[:128]
        if error_detail is not None:
            values["error_detail"] = error_detail[:1024]
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(InteractionTraceRecord)
                .where(InteractionTraceRecord.id == trace_id)
                .values(**values)
            )

    async def start_span(
        self,
        *,
        trace_id: str,
        component: str,
        operation: str,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> TraceSpanRef:
        start = started_at or datetime.now(UTC)
        async with self._database.session() as session, session.begin():
            last_sequence = await session.scalar(
                select(func.max(TraceSpanRecord.sequence)).where(
                    TraceSpanRecord.trace_id == trace_id
                )
            )
            row = TraceSpanRecord(
                trace_id=trace_id,
                parent_span_id=parent_span_id,
                sequence=int(last_sequence or 0) + 1,
                component=component,
                operation=operation,
                attributes_json=self._json(attributes or {}),
                started_at=start,
            )
            session.add(row)
        return TraceSpanRef(id=row.id, trace_id=trace_id, started_at=start)

    async def finish_span(
        self,
        span: TraceSpanRef,
        *,
        status: str = "completed",
        attributes: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "completed_at": completed_at or datetime.now(UTC),
        }
        if attributes is not None:
            values["attributes_json"] = self._json(attributes)
        if error_code is not None:
            values["error_code"] = error_code[:128]
        if error_detail is not None:
            values["error_detail"] = error_detail[:1024]
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(TraceSpanRecord)
                .where(TraceSpanRecord.id == span.id)
                .values(**values)
            )

    async def record_event(
        self,
        *,
        trace_id: str,
        component: str,
        operation: str,
        status: str = "completed",
        attributes: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        span = await self.start_span(
            trace_id=trace_id,
            component=component,
            operation=operation,
            attributes=attributes,
            started_at=now,
        )
        await self.finish_span(
            span,
            status=status,
            attributes=attributes,
            error_code=error_code,
            error_detail=error_detail,
            completed_at=now,
        )

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _new_trace_id() -> str:
        return str(uuid4())

    @classmethod
    async def _find_historical_turn(
        cls,
        *,
        session: AsyncSession,
        user_id: str,
        channel_id: str,
        source_message_id: str,
    ) -> AgentTurnRecord | None:
        rows = await session.execute(
            select(AgentItemRecord, AgentTurnRecord)
            .join(AgentTurnRecord, AgentTurnRecord.id == AgentItemRecord.turn_id)
            .join(AgentThreadRecord, AgentThreadRecord.id == AgentTurnRecord.thread_id)
            .where(
                AgentThreadRecord.user_id == user_id,
                AgentItemRecord.item_type.in_(("user_message", "image_attachment")),
            )
            .order_by(AgentItemRecord.created_at.desc())
        )
        expected_hash = hashlib.sha256(source_message_id.encode()).hexdigest()
        matches: dict[str, AgentTurnRecord] = {}
        for item, turn in rows:
            try:
                payload = json.loads(item.payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            source_matches = payload.get("source_message_id") == source_message_id or (
                payload.get("source_message_id_sha256") == expected_hash
            )
            channel_matches = payload.get("channel_id") in {None, channel_id}
            if source_matches and channel_matches:
                matches[turn.id] = turn
        return next(iter(matches.values())) if len(matches) == 1 else None
