from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, or_, select

from slim_guard.admin.presentation import context_sources, execution_summary, present_event
from slim_guard.db.models import (
    AdminAuditEventRecord,
    AgentItemRecord,
    AgentItemRedactionRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    ChannelIdentity,
    ExerciseRecord,
    InteractionTraceRecord,
    MealRecord,
    MemoryHandoffRecord,
    OutboundMessage,
    ProactiveMessageRecord,
    RoutineJobRecord,
    SlimGuardUser,
    ToolExecutionRecord,
    TraceSpanRecord,
    UserMemoryFactRecord,
    UserRoutinePreference,
    WeightRecord,
)
from slim_guard.db.session import Database


class AdminQueryRepository:
    """Builds privacy-aware user and trace views for the admin SPA."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_users(
        self,
        *,
        search: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        async with self._database.session() as session:
            conditions: list[Any] = []
            normalized = (search or "").strip()
            if normalized:
                conditions.append(
                    or_(
                        SlimGuardUser.nickname.ilike(f"%{normalized}%"),
                        SlimGuardUser.id == normalized,
                    )
                )
            count_statement = select(func.count(SlimGuardUser.id))
            statement = select(SlimGuardUser)
            if conditions:
                count_statement = count_statement.where(*conditions)
                statement = statement.where(*conditions)
            total = int(await session.scalar(count_statement) or 0)
            users = tuple(
                await session.scalars(
                    statement.order_by(
                        SlimGuardUser.last_seen_at.desc(), SlimGuardUser.id
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            if not users:
                return {"items": [], "total": total, "limit": limit, "offset": offset}
            user_ids = [user.id for user in users]
            identity_rows = await session.execute(
                select(ChannelIdentity.user_id, ChannelIdentity.external_userid).where(
                    ChannelIdentity.user_id.in_(user_ids)
                )
            )
            identity_refs: dict[str, list[str]] = {}
            for user_id, external_userid in identity_rows:
                identity_refs.setdefault(user_id, []).append(self._ref(external_userid))
            trace_rows = await session.execute(
                select(
                    InteractionTraceRecord.user_id,
                    func.count(InteractionTraceRecord.id),
                    func.sum(
                        case(
                            (
                                InteractionTraceRecord.generation_status.in_(
                                    ("failed", "degraded")
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.max(InteractionTraceRecord.created_at),
                )
                .where(InteractionTraceRecord.user_id.in_(user_ids))
                .group_by(InteractionTraceRecord.user_id)
            )
            trace_stats = {
                row[0]: {
                    "trace_count": int(row[1] or 0),
                    "issue_count": int(row[2] or 0),
                    "last_trace_at": row[3],
                }
                for row in trace_rows
            }
            latest_traces: dict[str, InteractionTraceRecord] = {}
            for trace in await session.scalars(
                select(InteractionTraceRecord)
                .where(InteractionTraceRecord.user_id.in_(user_ids))
                .order_by(
                    InteractionTraceRecord.created_at.desc(),
                    InteractionTraceRecord.id,
                )
            ):
                latest_traces.setdefault(trace.user_id, trace)
            items = []
            for user in users:
                stats = trace_stats.get(
                    user.id,
                    {"trace_count": 0, "issue_count": 0, "last_trace_at": None},
                )
                latest = latest_traces.get(user.id)
                items.append(
                    {
                        "id": user.id,
                        "user_ref": self._ref(user.id),
                        "external_refs": identity_refs.get(user.id, []),
                        "nickname": user.nickname,
                        "gender": user.gender,
                        "first_seen_at": user.first_seen_at,
                        "last_seen_at": user.last_seen_at,
                        "last_generation_status": (
                            latest.generation_status if latest is not None else None
                        ),
                        "last_delivery_status": (
                            latest.delivery_status if latest is not None else None
                        ),
                        **stats,
                    }
                )
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            user = await session.get(SlimGuardUser, user_id)
            if user is None:
                return None
            identities = tuple(
                await session.scalars(
                    select(ChannelIdentity).where(ChannelIdentity.user_id == user_id)
                )
            )
            counts = {}
            for key, model in (
                ("trace_count", InteractionTraceRecord),
                ("weight_count", WeightRecord),
                ("meal_count", MealRecord),
                ("exercise_count", ExerciseRecord),
                ("memory_count", UserMemoryFactRecord),
            ):
                count_query = select(func.count()).select_from(model).where(
                    model.user_id == user_id
                )
                if model is UserMemoryFactRecord:
                    count_query = count_query.where(UserMemoryFactRecord.status == "active")
                counts[key] = int(
                    await session.scalar(count_query)
                    or 0
                )
            active_handoff = await session.scalar(
                select(MemoryHandoffRecord).where(
                    MemoryHandoffRecord.user_id == user_id,
                    MemoryHandoffRecord.status == "active",
                )
            )
            routine = await session.get(UserRoutinePreference, user_id)
            return {
                "id": user.id,
                "user_ref": self._ref(user.id),
                "nickname": user.nickname,
                "gender": user.gender,
                "first_seen_at": user.first_seen_at,
                "last_seen_at": user.last_seen_at,
                "identities": [
                    {
                        "channel_id": identity.channel_id,
                        "external_ref": self._ref(identity.external_userid),
                        "profile_status": identity.profile_status,
                        "profile_synced_at": identity.profile_synced_at,
                    }
                    for identity in identities
                ],
                "counts": counts,
                "active_handoff": (
                    {
                        "id": active_handoff.id,
                        "objective": active_handoff.objective,
                        "unresolved": self._json_load(active_handoff.unresolved_json),
                        "expires_at": active_handoff.expires_at,
                    }
                    if active_handoff is not None
                    else None
                ),
                "routine": (
                    {
                        "timezone": routine.timezone,
                        "weight_reminder_time": routine.weight_reminder_time,
                        "meal_reminder_time": routine.meal_reminder_time,
                        "daily_review_time": routine.daily_review_time,
                    }
                    if routine is not None
                    else None
                ),
            }

    async def list_traces(
        self,
        *,
        user_id: str,
        limit: int,
        offset: int,
        generation_status: str | None = None,
        delivery_status: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._database.session() as session:
            if await session.get(SlimGuardUser, user_id) is None:
                return None
            filters = [InteractionTraceRecord.user_id == user_id]
            if generation_status:
                filters.append(
                    InteractionTraceRecord.generation_status == generation_status
                )
            if delivery_status:
                filters.append(InteractionTraceRecord.delivery_status == delivery_status)
            total = int(
                await session.scalar(
                    select(func.count(InteractionTraceRecord.id)).where(*filters)
                )
                or 0
            )
            traces = tuple(
                await session.scalars(
                    select(InteractionTraceRecord)
                    .where(*filters)
                    .order_by(
                        InteractionTraceRecord.created_at.desc(),
                        InteractionTraceRecord.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            return {
                "items": [self._trace_summary(trace) for trace in traces],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def get_trace(self, *, user_id: str, trace_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            trace = await session.get(InteractionTraceRecord, trace_id)
            if trace is None or trace.user_id != user_id:
                return None
            spans = tuple(
                await session.scalars(
                    select(TraceSpanRecord)
                    .where(TraceSpanRecord.trace_id == trace_id)
                    .order_by(TraceSpanRecord.sequence)
                )
            )
            turn = (
                await session.get(AgentTurnRecord, trace.agent_turn_id)
                if trace.agent_turn_id is not None
                else None
            )
            agent_version = (
                await session.get(AgentVersionRecord, turn.agent_version_id)
                if turn is not None
                else None
            )
            item_rows: list[tuple[AgentItemRecord, AgentItemRedactionRecord | None]] = []
            tool_rows: tuple[ToolExecutionRecord, ...] = ()
            if turn is not None:
                results = await session.execute(
                    select(AgentItemRecord, AgentItemRedactionRecord)
                    .outerjoin(
                        AgentItemRedactionRecord,
                        AgentItemRedactionRecord.item_id == AgentItemRecord.id,
                    )
                    .where(AgentItemRecord.turn_id == turn.id)
                    .order_by(AgentItemRecord.sequence)
                )
                item_rows = [(item, redaction) for item, redaction in results.tuples()]
                tool_rows = tuple(
                    await session.scalars(
                        select(ToolExecutionRecord)
                        .where(ToolExecutionRecord.turn_id == turn.id)
                        .order_by(ToolExecutionRecord.created_at)
                    )
                )
            outbound = (
                await session.get(OutboundMessage, trace.outbound_idempotency_key)
                if trace.outbound_idempotency_key is not None
                else None
            )
            proactive = (
                await session.get(ProactiveMessageRecord, trace.routine_job_id)
                if trace.routine_job_id is not None
                else None
            )
            timeline: list[dict[str, Any]] = [self._span_view(span) for span in spans]
            timeline.extend(
                self._item_view(item, redaction) for item, redaction in item_rows
            )
            timeline.sort(key=lambda event: (self._aware(event["started_at"]), event["sequence"]))
            for event in timeline:
                event["presentation"] = present_event(event)
            return {
                "trace": self._trace_summary(trace),
                "turn": (
                    {
                        "id": turn.id,
                        "thread_id": turn.thread_id,
                        "agent_version_id": turn.agent_version_id,
                        "trigger_type": turn.trigger_type,
                        "status": turn.status,
                        "step_count": turn.step_count,
                        "deadline_at": turn.deadline_at,
                        "created_at": turn.created_at,
                        "completed_at": turn.completed_at,
                    }
                    if turn is not None
                    else None
                ),
                "agent": self._agent_version_view(agent_version),
                "timeline": timeline,
                "execution_summary": execution_summary(timeline),
                "context_sources": context_sources(timeline),
                "tool_executions": [self._tool_view(tool) for tool in tool_rows],
                "output": (
                    {
                        "kind": "outbound",
                        "content": outbound.content,
                        "status": outbound.status,
                        "platform_msgid": outbound.platform_msgid,
                        "last_error": outbound.last_error,
                        "attempt_started_at": outbound.attempt_started_at,
                        "completed_at": outbound.completed_at,
                    }
                    if outbound is not None
                    else {
                        "kind": "proactive",
                        "content": proactive.content,
                        "status": proactive.status,
                        "platform_msgid": proactive.platform_msgid,
                        "last_error": proactive.last_error,
                        "attempt_started_at": proactive.attempt_started_at,
                        "completed_at": proactive.completed_at,
                    }
                    if proactive is not None
                    else None
                ),
                "privacy": {
                    "contains_sensitive_health_data": True,
                    "redacted_item_count": sum(
                        1 for _, redaction in item_rows if redaction is not None
                    ),
                },
            }

    async def list_memories(self, *, user_id: str) -> list[dict[str, Any]] | None:
        async with self._database.session() as session:
            if await session.get(SlimGuardUser, user_id) is None:
                return None
            rows = tuple(
                await session.scalars(
                    select(UserMemoryFactRecord)
                    .where(UserMemoryFactRecord.user_id == user_id)
                    .order_by(UserMemoryFactRecord.created_at.desc())
                )
            )
            return [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "memory_key": row.memory_key,
                    "value": self._json_load(row.value_json),
                    "status": row.status,
                    "assertion": row.assertion,
                    "sensitivity": row.sensitivity,
                    "source_turn_id": row.source_turn_id,
                    "valid_from": row.valid_from,
                    "expires_at": row.expires_at,
                    "review_after": row.review_after,
                    "ended_at": row.ended_at,
                }
                for row in rows
            ]

    async def list_records(self, *, user_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            if await session.get(SlimGuardUser, user_id) is None:
                return None
            weights = tuple(
                await session.scalars(
                    select(WeightRecord)
                    .where(WeightRecord.user_id == user_id)
                    .order_by(WeightRecord.measured_at.desc())
                    .limit(100)
                )
            )
            meals = tuple(
                await session.scalars(
                    select(MealRecord)
                    .where(MealRecord.user_id == user_id)
                    .order_by(MealRecord.occurred_at.desc())
                    .limit(100)
                )
            )
            exercises = tuple(
                await session.scalars(
                    select(ExerciseRecord)
                    .where(ExerciseRecord.user_id == user_id)
                    .order_by(ExerciseRecord.occurred_at.desc())
                    .limit(100)
                )
            )
            return {
                "weights": [
                    {
                        "id": row.id,
                        "weight_kg": row.weight_grams / 1000,
                        "measured_at": row.measured_at,
                        "condition": row.measurement_condition,
                        "status": row.status,
                        "source_turn_id": row.source_turn_id,
                    }
                    for row in weights
                ],
                "meals": [
                    {
                        "id": row.id,
                        "meal_type": row.meal_type,
                        "foods": self._json_load(row.foods_json),
                        "note": row.note,
                        "occurred_at": row.occurred_at,
                        "status": row.status,
                        "source_turn_id": row.source_turn_id,
                    }
                    for row in meals
                ],
                "exercises": [
                    {
                        "id": row.id,
                        "activity_name": row.activity_name,
                        "duration_minutes": row.duration_minutes,
                        "steps": row.steps,
                        "distance_meters": row.distance_meters,
                        "reported_energy_kcal": row.reported_energy_kcal,
                        "note": row.note,
                        "occurred_at": row.occurred_at,
                        "status": row.status,
                        "source_turn_id": row.source_turn_id,
                    }
                    for row in exercises
                ],
            }

    async def list_routines(self, *, user_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            if await session.get(SlimGuardUser, user_id) is None:
                return None
            preference = await session.get(UserRoutinePreference, user_id)
            jobs = tuple(
                await session.scalars(
                    select(RoutineJobRecord)
                    .where(RoutineJobRecord.user_id == user_id)
                    .order_by(RoutineJobRecord.scheduled_for.desc())
                    .limit(100)
                )
            )
            return {
                "preference": (
                    {
                        "timezone": preference.timezone,
                        "weight_reminder_time": preference.weight_reminder_time,
                        "meal_reminder_time": preference.meal_reminder_time,
                        "daily_review_time": preference.daily_review_time,
                    }
                    if preference is not None
                    else None
                ),
                "jobs": [
                    {
                        "id": row.id,
                        "job_kind": row.job_kind,
                        "local_date": row.local_date,
                        "scheduled_for": row.scheduled_for,
                        "status": row.status,
                        "attempt_count": row.attempt_count,
                        "result_turn_id": row.result_turn_id,
                        "result_code": row.result_code,
                        "completed_at": row.completed_at,
                    }
                    for row in jobs
                ],
            }

    async def audit(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        remote_ref: str | None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        async with self._database.session() as session, session.begin():
            session.add(
                AdminAuditEventRecord(
                    actor=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    user_id=user_id,
                    trace_id=trace_id,
                    remote_ref=remote_ref,
                )
            )

    @classmethod
    def _trace_summary(cls, trace: InteractionTraceRecord) -> dict[str, Any]:
        duration_ms = None
        if trace.completed_at is not None:
            duration_ms = int(
                (cls._aware(trace.completed_at) - cls._aware(trace.created_at)).total_seconds()
                * 1000
            )
        return {
            "id": trace.id,
            "user_id": trace.user_id,
            "trigger_type": trace.trigger_type,
            "channel_id": trace.channel_id,
            "inbound_msgid": trace.inbound_msgid,
            "agent_turn_id": trace.agent_turn_id,
            "agent_version_id": trace.agent_version_id,
            "reply_kind": trace.reply_kind,
            "generation_status": trace.generation_status,
            "delivery_status": trace.delivery_status,
            "failure_code": trace.failure_code,
            "error_detail": trace.error_detail,
            "created_at": trace.created_at,
            "completed_at": trace.completed_at,
            "duration_ms": duration_ms,
        }

    @classmethod
    def _span_view(cls, span: TraceSpanRecord) -> dict[str, Any]:
        duration_ms = None
        if span.completed_at is not None:
            duration_ms = int(
                (cls._aware(span.completed_at) - cls._aware(span.started_at)).total_seconds()
                * 1000
            )
        return {
            "event_type": "span",
            "id": span.id,
            "parent_span_id": span.parent_span_id,
            "sequence": span.sequence,
            "component": span.component,
            "operation": span.operation,
            "status": span.status,
            "details": cls._json_load(span.attributes_json),
            "error_code": span.error_code,
            "error_detail": span.error_detail,
            "started_at": span.started_at,
            "completed_at": span.completed_at,
            "duration_ms": duration_ms,
        }

    @classmethod
    def _item_view(
        cls,
        item: AgentItemRecord,
        redaction: AgentItemRedactionRecord | None,
    ) -> dict[str, Any]:
        details = cls._json_load(item.payload_json)
        started_at = item.created_at
        completed_at = None
        if isinstance(details, dict):
            payload_started_at = cls._parse_datetime(details.get("started_at"))
            payload_completed_at = cls._parse_datetime(details.get("completed_at"))
            if payload_started_at is not None and payload_completed_at is not None:
                started_at = payload_started_at
                completed_at = payload_completed_at
        duration_ms = None
        if completed_at is not None:
            duration_ms = max(
                0,
                int(
                    (cls._aware(completed_at) - cls._aware(started_at)).total_seconds()
                    * 1000
                ),
            )
        return {
            "event_type": "agent_item",
            "id": item.id,
            "sequence": 10000 + item.sequence,
            "component": "agent",
            "operation": item.item_type,
            "status": item.status,
            "details": details,
            "redacted": redaction is not None,
            "redaction_policy": redaction.policy_version if redaction is not None else None,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
        }

    @classmethod
    def _tool_view(cls, row: ToolExecutionRecord) -> dict[str, Any]:
        return {
            "idempotency_key": row.idempotency_key,
            "tool_call_id": row.tool_call_id,
            "tool_name": row.tool_name,
            "tool_version": row.tool_version,
            "arguments": cls._json_load(row.canonical_arguments_json),
            "status": row.status,
            "result": cls._json_load(row.result_json),
            "created_at": row.created_at,
            "completed_at": row.completed_at,
        }

    @classmethod
    def _agent_version_view(cls, row: AgentVersionRecord | None) -> dict[str, Any] | None:
        if row is None:
            return None
        manifest = cls._json_load(row.manifest_json)
        if not isinstance(manifest, dict):
            return None
        tools = manifest.get("tool_versions")
        return {
            "id": row.id,
            "model_provider": manifest.get("model_provider"),
            "text_model": manifest.get("text_model"),
            "vision_model": manifest.get("vision_model"),
            "system_prompt_version": manifest.get("system_prompt_version"),
            "context_policy_version": manifest.get("context_policy_version"),
            "memory_policy_version": manifest.get("memory_policy_version"),
            "safety_policy_version": manifest.get("safety_policy_version"),
            "code_revision": row.code_revision,
            "tool_count": len(tools) if isinstance(tools, list) else 0,
        }

    @staticmethod
    def _json_load(value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {"unparsed": True}

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _ref(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:12]

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
