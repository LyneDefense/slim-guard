from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select

from slim_guard.admin.presentation import context_sources, execution_summary, present_event
from slim_guard.agents.contracts import payload_sha256
from slim_guard.db.models import (
    AdminAuditEventRecord,
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentItemRecord,
    AgentItemRedactionRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    BodyFatRecord,
    ChannelIdentity,
    ExerciseRecord,
    InteractionTraceRecord,
    MealRecord,
    MemoryHandoffRecord,
    MemoryIndexOutboxRecord,
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
                    statement.order_by(SlimGuardUser.last_seen_at.desc(), SlimGuardUser.id)
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
                ("body_fat_count", BodyFatRecord),
                ("meal_count", MealRecord),
                ("exercise_count", ExerciseRecord),
                ("memory_count", UserMemoryFactRecord),
            ):
                count_query = (
                    select(func.count()).select_from(model).where(model.user_id == user_id)
                )
                if model is UserMemoryFactRecord:
                    count_query = count_query.where(UserMemoryFactRecord.status == "active")
                counts[key] = int(await session.scalar(count_query) or 0)
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
        mode: str | None = None,
        agent_failure: bool | None = None,
        rag: bool | None = None,
        repair: bool | None = None,
        degraded: bool | None = None,
        graph_version: str | None = None,
        agent_version: str | None = None,
        profile_version: str | None = None,
        dish_confirmation: str | None = None,
        dish_match: str | None = None,
        dish_suitability: str | None = None,
        review_verdict: str | None = None,
    ) -> dict[str, Any] | None:
        """List user traces, enriching only one SQL page unless facet filtering is used.

        Facet filters currently require bounded in-process enrichment of that user's
        matching history before pagination; the unfiltered path retains database
        count/offset/limit behavior.
        """

        async with self._database.session() as session:
            if await session.get(SlimGuardUser, user_id) is None:
                return None
            filters = [InteractionTraceRecord.user_id == user_id]
            if generation_status:
                filters.append(InteractionTraceRecord.generation_status == generation_status)
            if delivery_status:
                filters.append(InteractionTraceRecord.delivery_status == delivery_status)
            facet_filters_active = any(
                value is not None
                for value in (
                    mode,
                    agent_failure,
                    rag,
                    repair,
                    degraded,
                    graph_version,
                    agent_version,
                    profile_version,
                    dish_confirmation,
                    dish_match,
                    dish_suitability,
                    review_verdict,
                )
            )
            unfiltered_total = int(
                await session.scalar(select(func.count(InteractionTraceRecord.id)).where(*filters))
                or 0
            )
            trace_statement = (
                select(InteractionTraceRecord)
                .where(*filters)
                .order_by(
                    InteractionTraceRecord.created_at.desc(),
                    InteractionTraceRecord.id,
                )
            )
            if not facet_filters_active:
                trace_statement = trace_statement.offset(offset).limit(limit)
            traces = tuple(await session.scalars(trace_statement))
            trace_ids = [trace.id for trace in traces]
            turn_ids = list(
                dict.fromkeys(
                    trace.agent_turn_id for trace in traces if isinstance(trace.agent_turn_id, str)
                )
            )
            invocation_rows = (
                tuple(
                    await session.scalars(
                        select(AgentInvocationRecord)
                        .where(AgentInvocationRecord.trace_id.in_(trace_ids))
                        .order_by(
                            AgentInvocationRecord.started_at,
                            AgentInvocationRecord.id,
                        )
                    )
                )
                if trace_ids
                else ()
            )
            artifact_rows = (
                tuple(
                    await session.scalars(
                        select(AgentArtifactRecord)
                        .where(AgentArtifactRecord.turn_id.in_(turn_ids))
                        .order_by(
                            AgentArtifactRecord.created_at,
                            AgentArtifactRecord.id,
                        )
                    )
                )
                if turn_ids
                else ()
            )
            item_rows = (
                tuple(
                    await session.scalars(
                        select(AgentItemRecord)
                        .where(
                            AgentItemRecord.turn_id.in_(turn_ids),
                            AgentItemRecord.item_type.in_(
                                (
                                    "workflow_transition",
                                    "response_adopted",
                                    "response_degraded",
                                )
                            ),
                        )
                        .order_by(AgentItemRecord.created_at, AgentItemRecord.sequence)
                    )
                )
                if turn_ids
                else ()
            )

        invocations_by_trace: dict[str, list[dict[str, Any]]] = {}
        for invocation_row in invocation_rows:
            invocation = self._invocation_view(invocation_row, started_event=None)
            invocations_by_trace.setdefault(invocation_row.trace_id, []).append(invocation)
        artifacts_by_turn: dict[str, list[dict[str, Any]]] = {}
        for artifact_row in artifact_rows:
            artifacts_by_turn.setdefault(artifact_row.turn_id, []).append(
                self._artifact_view(artifact_row)
            )
        events_by_turn: dict[str, list[dict[str, Any]]] = {}
        for item in item_rows:
            events_by_turn.setdefault(item.turn_id, []).append(self._item_view(item, None))

        enriched: list[dict[str, Any]] = []
        for trace in traces:
            turn_id = trace.agent_turn_id or ""
            facets = self._trace_workflow_facets(
                trace=trace,
                invocations=invocations_by_trace.get(trace.id, []),
                artifacts=artifacts_by_turn.get(turn_id, []),
                timeline=events_by_turn.get(turn_id, []),
            )
            if not self._matches_trace_facets(
                facets,
                mode=mode,
                agent_failure=agent_failure,
                rag=rag,
                repair=repair,
                degraded=degraded,
                graph_version=graph_version,
                agent_version=agent_version,
                profile_version=profile_version,
                dish_confirmation=dish_confirmation,
                dish_match=dish_match,
                dish_suitability=dish_suitability,
                review_verdict=review_verdict,
            ):
                continue
            enriched.append({**self._trace_summary(trace), **facets})

        return {
            "items": (enriched[offset : offset + limit] if facet_filters_active else enriched),
            "total": len(enriched) if facet_filters_active else unfiltered_total,
            "limit": limit,
            "offset": offset,
        }

    @classmethod
    def _trace_workflow_facets(
        cls,
        *,
        trace: InteractionTraceRecord,
        invocations: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        adopted_events = cls._workflow_events(timeline, "response_adopted")
        adopted = adopted_events[-1].get("details") if adopted_events else None
        raw_mode = adopted.get("mode") if isinstance(adopted, dict) else None
        invocation_modes = cls._unique_string_field(invocations, "workflow_mode")
        mode = (
            raw_mode
            if isinstance(raw_mode, str)
            else invocation_modes[0]
            if invocation_modes
            else "shadow"
            if invocations
            else "off"
        )
        if mode == "legacy":
            mode = "off"

        graph_versions = cls._unique_string_field(invocations, "graph_version")
        agent_versions = cls._unique_string_field(invocations, "agent_version")
        profile_versions = cls._unique_string_field(artifacts, "style_profile_version")
        dish_confirmation, dish_matches, dish_suitabilities = cls._dish_facets(artifacts)
        review_verdict = cls._review_verdict_facet(artifacts)
        transition_events = cls._workflow_events(timeline, "workflow_transition")
        repair = any(
            isinstance(event.get("details"), dict)
            and cls._is_review_repair_transition(event["details"])
            for event in transition_events
        )
        degraded = (
            trace.generation_status == "degraded"
            or any(invocation.get("status") == "degraded" for invocation in invocations)
            or bool(cls._workflow_events(timeline, "response_degraded"))
        )
        return {
            "mode": mode,
            "graph_version": graph_versions[-1] if graph_versions else None,
            "agent_versions": agent_versions,
            "profile_versions": profile_versions,
            "agent_failure": any(
                invocation.get("status") == "failed" for invocation in invocations
            ),
            "rag": any(cls._artifact_used_rag(artifact) for artifact in artifacts),
            "repair": repair,
            "degraded": degraded,
            "dish_confirmation": dish_confirmation,
            "dish_matches": dish_matches,
            "dish_suitabilities": dish_suitabilities,
            "review_verdict": review_verdict,
        }

    @staticmethod
    def _unique_string_field(items: list[dict[str, Any]], field: str) -> list[str]:
        return list(
            dict.fromkeys(
                value for item in items if isinstance((value := item.get(field)), str) and value
            )
        )

    @classmethod
    def _artifact_used_rag(cls, artifact: dict[str, Any]) -> bool:
        if cls._normalized_artifact_type(str(artifact.get("artifact_type", ""))) not in {
            "nutritionobservation",
            "nutritionobservations",
            "dishevidencebundle",
        }:
            return False
        payload = artifact.get("payload")
        rag_enabled = payload.get("rag_enabled") if isinstance(payload, dict) else None
        if isinstance(rag_enabled, bool):
            return rag_enabled
        if cls._normalized_artifact_type(str(artifact.get("artifact_type", ""))) == (
            "dishevidencebundle"
        ):
            dishes = payload.get("dishes") if isinstance(payload, dict) else None
            return isinstance(dishes, list) and any(
                isinstance(dish, dict) and bool(dish.get("rag_evidence") or dish.get("citations"))
                for dish in dishes
            )
        knowledge = payload.get("knowledge") if isinstance(payload, dict) else None
        if not isinstance(knowledge, dict):
            return False
        corpus_status = str(knowledge.get("corpus_status", "")).lower()
        if corpus_status in {"available", "ready"}:
            return True
        return any(
            isinstance(knowledge.get(key), list) and bool(knowledge[key])
            for key in ("candidates", "citations", "adopted_citations")
        )

    @staticmethod
    def _matches_trace_facets(
        facets: dict[str, Any],
        *,
        mode: str | None,
        agent_failure: bool | None,
        rag: bool | None,
        repair: bool | None,
        degraded: bool | None,
        graph_version: str | None,
        agent_version: str | None,
        profile_version: str | None,
        dish_confirmation: str | None,
        dish_match: str | None,
        dish_suitability: str | None,
        review_verdict: str | None,
    ) -> bool:
        boolean_filters = {
            "agent_failure": agent_failure,
            "rag": rag,
            "repair": repair,
            "degraded": degraded,
        }
        if any(
            expected is not None and facets.get(key) is not expected
            for key, expected in boolean_filters.items()
        ):
            return False
        if mode is not None and facets.get("mode") != mode:
            return False
        if graph_version is not None and facets.get("graph_version") != graph_version:
            return False
        if agent_version is not None and agent_version not in facets.get("agent_versions", []):
            return False
        if dish_confirmation is not None and facets.get("dish_confirmation") != dish_confirmation:
            return False
        if dish_match is not None and dish_match not in facets.get("dish_matches", []):
            return False
        if dish_suitability is not None and dish_suitability not in facets.get(
            "dish_suitabilities", []
        ):
            return False
        if review_verdict is not None and facets.get("review_verdict") != review_verdict:
            return False
        return not (
            profile_version is not None
            and profile_version not in facets.get("profile_versions", [])
        )

    @classmethod
    def _dish_facets(
        cls,
        artifacts: list[dict[str, Any]],
    ) -> tuple[str, list[str], list[str]]:
        confirmation = "not_applicable"
        matches: list[str] = []
        suitabilities: list[str] = []
        for artifact in artifacts:
            payload = artifact.get("payload")
            if not isinstance(payload, dict):
                continue
            normalized = cls._normalized_artifact_type(str(artifact.get("artifact_type", "")))
            if normalized == "dishrecognition":
                confirmation = (
                    "pending"
                    if payload.get("overall_requires_confirmation") is True
                    else "automatic"
                )
            elif normalized == "confirmeddishset":
                confirmation = "confirmed"
            elif normalized == "dishevidencebundle":
                dishes = payload.get("dishes")
                if isinstance(dishes, list):
                    for dish in dishes:
                        entity_match = dish.get("entity_match") if isinstance(dish, dict) else None
                        status = (
                            entity_match.get("status") if isinstance(entity_match, dict) else None
                        )
                        if isinstance(status, str) and status not in matches:
                            matches.append(status)
            elif normalized == "dietguidanceassessment":
                dishes = payload.get("dishes")
                if isinstance(dishes, list):
                    for dish in dishes:
                        value = dish.get("suitability") if isinstance(dish, dict) else None
                        if isinstance(value, str) and value not in suitabilities:
                            suitabilities.append(value)
        return confirmation, matches, suitabilities

    @classmethod
    def _review_verdict_facet(cls, artifacts: list[dict[str, Any]]) -> str | None:
        verdicts = [
            artifact.get("payload", {}).get("verdict")
            for artifact in artifacts
            if cls._normalized_artifact_type(str(artifact.get("artifact_type", "")))
            == "reviewerverdict"
            and isinstance(artifact.get("payload"), dict)
        ]
        return next((value for value in reversed(verdicts) if isinstance(value, str)), None)

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
            invocation_rows: tuple[AgentInvocationRecord, ...] = ()
            artifact_rows: tuple[AgentArtifactRecord, ...] = ()
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
                invocation_rows = tuple(
                    await session.scalars(
                        select(AgentInvocationRecord)
                        .where(AgentInvocationRecord.turn_id == turn.id)
                        .order_by(
                            AgentInvocationRecord.started_at,
                            AgentInvocationRecord.id,
                        )
                    )
                )
                artifact_rows = tuple(
                    await session.scalars(
                        select(AgentArtifactRecord)
                        .where(AgentArtifactRecord.turn_id == turn.id)
                        .order_by(
                            AgentArtifactRecord.created_at,
                            AgentArtifactRecord.id,
                        )
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
            timeline.extend(self._item_view(item, redaction) for item, redaction in item_rows)
            timeline.sort(key=lambda event: (self._aware(event["started_at"]), event["sequence"]))
            for event in timeline:
                event["presentation"] = present_event(event)
            output = (
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
            )
            workflow = self._workflow_view(
                trace=trace,
                invocation_rows=invocation_rows,
                artifact_rows=artifact_rows,
                timeline=timeline,
                output=output,
            )
            trace_facets = self._trace_workflow_facets(
                trace=trace,
                invocations=workflow["invocations"],
                artifacts=workflow["artifacts"],
                timeline=timeline,
            )
            return {
                "trace": {**self._trace_summary(trace), **trace_facets},
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
                "output": output,
                "workflow": workflow,
                # Top-level aliases keep early Increment 1 clients compatible.
                "invocations": workflow["invocations"],
                "artifacts": workflow["artifacts"],
                "transitions": workflow["transitions"],
                "shadow_comparison": workflow["shadow_comparison"],
                "evidence": workflow["evidence"],
                "review": workflow["review"],
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
            sync_rows = tuple(
                await session.scalars(
                    select(MemoryIndexOutboxRecord)
                    .where(MemoryIndexOutboxRecord.user_id == user_id)
                    .order_by(MemoryIndexOutboxRecord.created_at.desc())
                )
            )
            latest_sync: dict[str, MemoryIndexOutboxRecord] = {}
            for sync in sync_rows:
                if sync.memory_id is not None:
                    latest_sync.setdefault(sync.memory_id, sync)
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
                    "source_item_id": row.source_item_id,
                    "evidence_item_id": row.evidence_item_id,
                    "valid_from": row.valid_from,
                    "expires_at": row.expires_at,
                    "review_after": row.review_after,
                    "ended_at": row.ended_at,
                    "semantic_index": self._memory_sync_view(latest_sync.get(row.id)),
                }
                for row in rows
            ]

    @staticmethod
    def _memory_sync_view(
        row: MemoryIndexOutboxRecord | None,
    ) -> dict[str, Any]:
        if row is None:
            return {"provider": "disabled_or_not_queued", "status": "not_queued"}
        return {
            "provider": "mem0",
            "operation": row.operation,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "error_code": row.error_code,
            "error_detail": row.error_detail,
            "updated_at": row.updated_at,
        }

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
            body_fat = tuple(
                await session.scalars(
                    select(BodyFatRecord)
                    .where(BodyFatRecord.user_id == user_id)
                    .order_by(BodyFatRecord.measured_at.desc())
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
                "body_fat": [
                    {
                        "id": row.id,
                        "body_fat_percent": row.body_fat_basis_points / 100,
                        "measured_at": row.measured_at,
                        "status": row.status,
                        "source_turn_id": row.source_turn_id,
                    }
                    for row in body_fat
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

    async def workflow_review_metrics(
        self,
        *,
        window_days: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate privacy-safe reviewer outcomes over a bounded UTC window."""

        end_at = self._aware(now or datetime.now(UTC))
        start_at = end_at - timedelta(days=window_days)
        async with self._database.session() as session:
            traces = tuple(
                await session.scalars(
                    select(InteractionTraceRecord)
                    .where(
                        InteractionTraceRecord.created_at >= start_at,
                        InteractionTraceRecord.created_at <= end_at,
                    )
                    .order_by(InteractionTraceRecord.created_at, InteractionTraceRecord.id)
                )
            )
            trace_ids = [trace.id for trace in traces]
            turn_ids = list(
                dict.fromkeys(
                    trace.agent_turn_id for trace in traces if isinstance(trace.agent_turn_id, str)
                )
            )
            invocation_rows = (
                tuple(
                    await session.scalars(
                        select(AgentInvocationRecord)
                        .where(AgentInvocationRecord.trace_id.in_(trace_ids))
                        .order_by(
                            AgentInvocationRecord.started_at,
                            AgentInvocationRecord.id,
                        )
                    )
                )
                if trace_ids
                else ()
            )
            artifact_rows = (
                tuple(
                    await session.scalars(
                        select(AgentArtifactRecord)
                        .where(AgentArtifactRecord.turn_id.in_(turn_ids))
                        .order_by(
                            AgentArtifactRecord.created_at,
                            AgentArtifactRecord.id,
                        )
                    )
                )
                if turn_ids
                else ()
            )
            item_rows = (
                tuple(
                    await session.scalars(
                        select(AgentItemRecord)
                        .where(
                            AgentItemRecord.turn_id.in_(turn_ids),
                            AgentItemRecord.item_type.in_(
                                (
                                    "model_message",
                                    "workflow_transition",
                                    "response_adopted",
                                    "response_degraded",
                                )
                            ),
                        )
                        .order_by(
                            AgentItemRecord.created_at,
                            AgentItemRecord.sequence,
                        )
                    )
                )
                if turn_ids
                else ()
            )

        invocations_by_turn: dict[str, list[dict[str, Any]]] = {}
        for invocation_row in invocation_rows:
            invocation = self._invocation_view(invocation_row, started_event=None)
            invocations_by_turn.setdefault(invocation_row.turn_id, []).append(invocation)
        artifacts_by_turn: dict[str, list[dict[str, Any]]] = {}
        for artifact_row in artifact_rows:
            artifacts_by_turn.setdefault(artifact_row.turn_id, []).append(
                self._artifact_view(artifact_row)
            )
        transitions_by_turn: dict[str, list[dict[str, Any]]] = {}
        degraded_by_turn: dict[str, list[dict[str, Any]]] = {}
        timeline_by_turn: dict[str, list[dict[str, Any]]] = {}
        legacy_tokens_by_turn: dict[str, int] = {}
        for item_row in item_rows:
            if item_row.item_type == "model_message":
                legacy_tokens_by_turn[item_row.turn_id] = legacy_tokens_by_turn.get(
                    item_row.turn_id, 0
                ) + self._model_message_token_count(item_row)
                continue
            timeline_by_turn.setdefault(item_row.turn_id, []).append(
                self._item_view(item_row, None)
            )
            details = self._admin_safe(self._json_load(item_row.payload_json))
            if not isinstance(details, dict):
                continue
            if item_row.item_type == "workflow_transition":
                transitions_by_turn.setdefault(item_row.turn_id, []).append(
                    {
                        "from_node": details.get("from_node"),
                        "to_node": details.get("to_node"),
                        "transition_type": details.get("transition_type"),
                        "reason_code": details.get("reason_code"),
                        "attempt": details.get("attempt"),
                    }
                )
            elif item_row.item_type == "response_degraded":
                degraded_by_turn.setdefault(item_row.turn_id, []).append({"details": details})

        workflows: list[dict[str, Any]] = []
        seen_turn_ids: set[str] = set()
        for trace in traces:
            turn_id = trace.agent_turn_id
            if not isinstance(turn_id, str) or turn_id in seen_turn_ids:
                continue
            invocations = invocations_by_turn.get(turn_id, [])
            if not invocations:
                continue
            seen_turn_ids.add(turn_id)
            artifacts = artifacts_by_turn.get(turn_id, [])
            review = self._review_summary(
                artifacts=artifacts_by_turn.get(turn_id, []),
                invocations=invocations,
                transitions=transitions_by_turn.get(turn_id, []),
                adopted={},
                degraded_events=degraded_by_turn.get(turn_id, []),
            )
            workflows.append(
                {
                    "turn_id": turn_id,
                    "trace": trace,
                    "invocations": invocations,
                    "artifacts": artifacts,
                    "review": review,
                    "evidence": self._evidence_summary(artifacts),
                    "facets": self._trace_workflow_facets(
                        trace=trace,
                        invocations=invocations,
                        artifacts=artifacts,
                        timeline=timeline_by_turn.get(turn_id, []),
                    ),
                }
            )

        summaries = [
            workflow["review"]
            for workflow in workflows
            if workflow["review"]["reviewer_invocation_count"]
            or workflow["review"]["verdict_count"]
        ]

        reviewed_workflow_count = len(summaries)
        rejected_workflow_count = sum(bool(item["rejected"]) for item in summaries)
        repair_workflow_count = sum(bool(item["repair_attempts"]) for item in summaries)
        degraded_workflow_count = sum(bool(item["degraded"]) for item in summaries)
        denominators = {
            "rejection_rate": reviewed_workflow_count,
            "repair_rate": reviewed_workflow_count,
            "degradation_rate": reviewed_workflow_count,
        }

        def rate(numerator: int, denominator: int) -> float:
            return numerator / denominator if denominator else 0.0

        target_names = ("orchestrator", "nutrition_expert", "response_style", "unknown")
        latency_values = [
            duration
            for workflow in workflows
            if isinstance(
                (duration := self._trace_summary(workflow["trace"])["duration_ms"]),
                int,
            )
        ]
        token_values = [
            (
                legacy_tokens_by_turn.get(str(workflow["turn_id"]), 0)
                + sum(
                    int(invocation.get("total_token_count") or 0)
                    for invocation in workflow["invocations"]
                )
            )
            for workflow in workflows
        ]
        node_counts: dict[str, dict[str, int]] = {}
        for workflow in workflows:
            for invocation in workflow["invocations"]:
                role = str(invocation.get("agent_role") or "unknown")
                counts = node_counts.setdefault(role, {"failed": 0, "total": 0})
                counts["total"] += 1
                if invocation.get("status") == "failed":
                    counts["failed"] += 1
        node_failure_rates = {
            role: {
                **counts,
                "rate": rate(counts["failed"], counts["total"]),
            }
            for role, counts in sorted(node_counts.items())
        }
        citation_metrics = self._citation_metrics([workflow["evidence"] for workflow in workflows])
        outcomes_by_mode: dict[str, dict[str, int]] = {}
        workflow_by_turn_id = {str(workflow["turn_id"]): workflow for workflow in workflows}
        outcome_keys: set[str] = set()
        for trace in traces:
            turn_id = trace.agent_turn_id
            outcome_key = f"turn:{turn_id}" if isinstance(turn_id, str) else f"trace:{trace.id}"
            if outcome_key in outcome_keys:
                continue
            outcome_keys.add(outcome_key)
            selected_workflow = (
                workflow_by_turn_id.get(turn_id) if isinstance(turn_id, str) else None
            )
            mode_name = (
                str(selected_workflow["facets"]["mode"]) if selected_workflow is not None else "off"
            )
            outcomes = outcomes_by_mode.setdefault(
                mode_name,
                {"total": 0, "succeeded": 0, "degraded": 0, "failed": 0},
            )
            outcomes["total"] += 1
            status_name = trace.generation_status
            if status_name in {"succeeded", "degraded", "failed"}:
                outcomes[status_name] += 1
        return {
            "window": {
                "days": window_days,
                "start_at": start_at,
                "end_at": end_at,
                "timezone": "UTC",
            },
            "counts": {
                "trace_count": len(traces),
                "workflow_count": len(workflows),
                "reviewed_workflow_count": reviewed_workflow_count,
                "reviewer_invocation_count": sum(
                    int(item["reviewer_invocation_count"]) for item in summaries
                ),
                "verdict_count": sum(int(item["verdict_count"]) for item in summaries),
                "issue_count": sum(int(item["issue_count"]) for item in summaries),
                "rejected_workflow_count": rejected_workflow_count,
                "repair_workflow_count": repair_workflow_count,
                "degraded_workflow_count": degraded_workflow_count,
                "repair_attempt_count": sum(len(item["repair_attempts"]) for item in summaries),
            },
            "rates": {
                "rejection_rate": rate(
                    rejected_workflow_count,
                    denominators["rejection_rate"],
                ),
                "repair_rate": rate(
                    repair_workflow_count,
                    denominators["repair_rate"],
                ),
                "degradation_rate": rate(
                    degraded_workflow_count,
                    denominators["degradation_rate"],
                ),
            },
            "denominators": denominators,
            "by_repair_target": {
                target: sum(int(item["repair_counts"].get(target, 0)) for item in summaries)
                for target in target_names
            },
            "latency_ms": {
                "sample_count": len(latency_values),
                "p50": self._percentile(latency_values, 50),
                "p95": self._percentile(latency_values, 95),
            },
            "tokens": {
                "workflow_count": len(token_values),
                "total": sum(token_values),
                "p50": self._percentile(token_values, 50),
                "p95": self._percentile(token_values, 95),
            },
            "node_failure_rates": node_failure_rates,
            "citations": citation_metrics,
            "outcomes_by_mode": outcomes_by_mode,
        }

    @classmethod
    def _model_message_token_count(cls, item: AgentItemRecord) -> int:
        payload = cls._json_load(item.payload_json)
        usage = payload.get("usage") if isinstance(payload, dict) else None
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else 0

    @staticmethod
    def _percentile(values: list[int], percentile: int) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * percentile / 100
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(ordered) - 1)
        fraction = position - lower_index
        return float(
            ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
        )

    @classmethod
    def _citation_metrics(
        cls,
        evidence_summaries: list[dict[str, Any]],
    ) -> dict[str, int | float]:
        knowledge_claim_count = 0
        covered_claim_count = 0
        citation_count = 0
        invalid_citation_count = 0
        for evidence in evidence_summaries:
            citations = evidence.get("adopted_citations")
            citations = citations if isinstance(citations, list) else []
            unresolved = cls._safe_string_values(evidence.get("unresolved_knowledge_refs"))
            citation_count += len(citations) + len(unresolved)
            valid_refs: set[str] = set()
            for citation in citations:
                if not isinstance(citation, dict):
                    invalid_citation_count += 1
                    continue
                invalid = cls._citation_is_explicitly_invalid(citation)
                if invalid:
                    invalid_citation_count += 1
                    continue
                valid_refs.update(
                    value
                    for value in (
                        citation.get("citation_id"),
                        citation.get("source_id"),
                        citation.get("chunk_id"),
                    )
                    if isinstance(value, str)
                )
            invalid_citation_count += len(unresolved)

            claims = evidence.get("claims")
            claims = claims if isinstance(claims, list) else []
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                knowledge_refs = cls._safe_string_values(claim.get("knowledge_refs"))
                basis_types = {
                    value.lower() for value in cls._safe_string_values(claim.get("basis_types"))
                }
                if not knowledge_refs and "rag_evidence" not in basis_types:
                    continue
                knowledge_claim_count += 1
                if any(reference in valid_refs for reference in knowledge_refs):
                    covered_claim_count += 1

        return {
            "knowledge_claim_count": knowledge_claim_count,
            "covered_claim_count": covered_claim_count,
            "citation_count": citation_count,
            "invalid_citation_count": invalid_citation_count,
            "coverage_rate": (
                covered_claim_count / knowledge_claim_count if knowledge_claim_count else 0.0
            ),
            "invalid_rate": (invalid_citation_count / citation_count if citation_count else 0.0),
        }

    @staticmethod
    def _citation_is_explicitly_invalid(citation: dict[str, Any]) -> bool:
        review_status = citation.get("review_status")
        publication_status = citation.get("publication_status")
        return (
            isinstance(review_status, str)
            and review_status.lower() != "approved"
            or isinstance(publication_status, str)
            and publication_status.lower() in {"rejected", "retired"}
            or citation.get("active") is False
        )

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
    def _workflow_view(
        cls,
        *,
        trace: InteractionTraceRecord,
        invocation_rows: tuple[AgentInvocationRecord, ...],
        artifact_rows: tuple[AgentArtifactRecord, ...],
        timeline: list[dict[str, Any]],
        output: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started_events = cls._workflow_events(timeline, "invocation_started")
        result_events = cls._workflow_events(timeline, "invocation_result")
        started_by_id = {
            event["details"].get("invocation_id"): event
            for event in started_events
            if isinstance(event.get("details"), dict)
        }
        result_by_id = {
            event["details"].get("invocation_id"): event
            for event in result_events
            if isinstance(event.get("details"), dict)
        }

        invocations = [
            cls._invocation_view(
                row,
                started_event=started_by_id.get(row.id),
            )
            for row in invocation_rows
        ]
        persisted_ids = {item["invocation_id"] for item in invocations}
        for event in started_events:
            details = event.get("details")
            if not isinstance(details, dict):
                continue
            invocation_id = details.get("invocation_id")
            if not isinstance(invocation_id, str) or invocation_id in persisted_ids:
                continue
            invocations.append(
                cls._event_invocation_view(
                    event,
                    result_by_id.get(invocation_id),
                )
            )
        invocations.sort(
            key=lambda item: (
                cls._aware(cls._parse_datetime(item["started_at"]) or trace.created_at),
                item["invocation_id"],
            )
        )

        artifacts = [cls._artifact_view(row) for row in artifact_rows]
        persisted_artifact_ids = {item["artifact_id"] for item in artifacts}
        for event in cls._workflow_events(timeline, "artifact_created"):
            details = event.get("details")
            if not isinstance(details, dict):
                continue
            artifact_id = details.get("artifact_id")
            if not isinstance(artifact_id, str) or artifact_id in persisted_artifact_ids:
                continue
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "invocation_id": None,
                    "artifact_type": details.get("artifact_type"),
                    "producer_role": details.get("producer_role"),
                    "schema_version": details.get("schema_version"),
                    "parent_artifact_ids": cls._string_list(details.get("parent_artifact_ids")),
                    "payload_sha256": details.get("payload_sha256"),
                    "payload": None,
                    "integrity_status": "metadata_only",
                    "created_at": event.get("started_at"),
                }
            )

        transitions = [
            {
                "from_node": event["details"].get("from_node"),
                "to_node": event["details"].get("to_node"),
                "transition_type": event["details"].get("transition_type"),
                "reason_code": event["details"].get("reason_code"),
                "attempt": event["details"].get("attempt"),
            }
            for event in cls._workflow_events(timeline, "workflow_transition")
            if isinstance(event.get("details"), dict)
        ]
        adopted_events = cls._workflow_events(timeline, "response_adopted")
        degraded_events = cls._workflow_events(timeline, "response_degraded")
        adopted = adopted_events[-1]["details"] if adopted_events else {}
        mode_value = adopted.get("mode") if isinstance(adopted, dict) else None
        invocation_modes = cls._unique_string_field(invocations, "workflow_mode")
        mode = (
            mode_value
            if isinstance(mode_value, str)
            else invocation_modes[0]
            if invocation_modes
            else "shadow"
            if invocations
            else "legacy"
        )
        degraded = bool(degraded_events) or any(
            item["status"] == "degraded" for item in invocations
        )
        statuses = {item["status"] for item in invocations}
        if not invocations:
            workflow_status = "legacy"
        elif degraded:
            workflow_status = "degraded"
        elif "started" in statuses:
            workflow_status = "running"
        elif "failed" in statuses:
            workflow_status = "failed"
        elif statuses == {"succeeded"}:
            workflow_status = "succeeded"
        else:
            workflow_status = trace.generation_status

        graph_version = next(
            (row.graph_version for row in invocation_rows if row.graph_version),
            "legacy",
        )
        repair_count = sum(cls._is_review_repair_transition(item) for item in transitions)
        style = cls._style_summary(
            artifacts=artifacts,
            transitions=transitions,
            adopted=adopted if isinstance(adopted, dict) else {},
            degraded_events=degraded_events,
        )
        adopted_artifact_id = style["adopted_artifact_id"]
        for artifact in artifacts:
            if str(artifact["artifact_type"]).lower() not in {
                "styledresponse",
                "styled_response",
                "neutral_response",
            }:
                continue
            artifact["adoption"] = {
                "status": (
                    style["status"]
                    if artifact["artifact_id"] == adopted_artifact_id
                    else "not_adopted"
                ),
                "mode": style["adoption_mode"],
                "final": style["final"],
            }
        summary = {
            "mode": mode,
            "graph_version": graph_version,
            "status": workflow_status,
            "model_call_count": sum(int(item["model_call_count"] or 0) for item in invocations),
            "tool_call_count": sum(int(item["tool_call_count"] or 0) for item in invocations),
            "total_token_count": sum(int(item["total_token_count"] or 0) for item in invocations),
            "repair_count": repair_count,
            "degraded": degraded,
            "style_profile_version": style["style_profile_version"],
            "style_status": style["status"],
            "style_bypassed": style["bypassed"],
            "style_degraded": style["degraded"],
            "style_adopted": style["adopted"],
        }
        shadow_comparison = (
            cls._shadow_comparison(
                mode=mode,
                adopted=adopted if isinstance(adopted, dict) else {},
                artifacts=artifacts,
                artifact_rows=artifact_rows,
                invocations=invocations,
                output=output,
            )
            if mode == "shadow"
            else None
        )
        evidence = cls._evidence_summary(artifacts)
        review = cls._review_summary(
            artifacts=artifacts,
            invocations=invocations,
            transitions=transitions,
            adopted=adopted if isinstance(adopted, dict) else {},
            degraded_events=degraded_events,
        )
        return {
            "summary": summary,
            "invocations": invocations,
            "artifacts": artifacts,
            "transitions": transitions,
            "shadow_comparison": shadow_comparison,
            "style": style,
            "evidence": evidence,
            "review": review,
        }

    @classmethod
    def _invocation_view(
        cls,
        row: AgentInvocationRecord,
        *,
        started_event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started_at = cls._aware(row.started_at)
        completed_at = cls._aware(row.completed_at) if row.completed_at is not None else None
        reason_summary = None
        if started_event is not None and isinstance(started_event.get("details"), dict):
            reason_summary = started_event["details"].get("reason_summary")
        input_payload = cls._json_load(row.input_payload_json)
        workflow_mode = input_payload.get("mode") if isinstance(input_payload, dict) else None
        workflow_mode = (
            workflow_mode if workflow_mode in {"off", "shadow", "canary", "on"} else None
        )
        return {
            "invocation_id": row.id,
            "graph_version": row.graph_version,
            "workflow_mode": workflow_mode,
            "agent_role": row.agent_role,
            "agent_version": row.agent_version,
            "attempt": row.attempt,
            "parent_invocation_id": row.parent_invocation_id,
            "input_artifact_ids": cls._string_list(cls._json_load(row.input_artifact_ids_json)),
            "output_artifact_id": row.output_artifact_id,
            "status": row.status,
            "model_call_count": row.model_call_count,
            "tool_call_count": row.tool_call_count,
            "total_token_count": row.total_token_count,
            "failure_code": row.failure_code,
            "failure_reason": row.failure_code,
            "reason_summary": reason_summary or row.reason_summary,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": cls._duration_ms(started_at, completed_at),
        }

    @classmethod
    def _event_invocation_view(
        cls,
        started_event: dict[str, Any],
        result_event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started = started_event["details"]
        result = (
            result_event["details"]
            if result_event is not None and isinstance(result_event.get("details"), dict)
            else {}
        )
        started_at = cls._parse_datetime(started.get("started_at"))
        completed_at = cls._parse_datetime(result.get("completed_at"))
        return {
            "invocation_id": started.get("invocation_id"),
            "graph_version": started.get("graph_version"),
            "workflow_mode": None,
            "agent_role": started.get("agent_role"),
            "agent_version": started.get("agent_version"),
            "attempt": started.get("attempt"),
            "parent_invocation_id": started.get("parent_invocation_id"),
            "input_artifact_ids": cls._string_list(started.get("input_artifact_ids")),
            "output_artifact_id": result.get("output_artifact_id"),
            "status": result.get("status") or "started",
            "model_call_count": int(result.get("model_call_count") or 0),
            "tool_call_count": int(result.get("tool_call_count") or 0),
            "total_token_count": int(result.get("total_token_count") or 0),
            "failure_code": result.get("failure_code"),
            "failure_reason": result.get("failure_code"),
            "reason_summary": started.get("reason_summary"),
            "started_at": started_at or started_event.get("started_at"),
            "completed_at": completed_at,
            "duration_ms": cls._duration_ms(started_at, completed_at),
        }

    @classmethod
    def _artifact_view(cls, row: AgentArtifactRecord) -> dict[str, Any]:
        payload = cls._json_load(row.payload_json)
        parents = cls._string_list(cls._json_load(row.parent_artifact_ids_json))
        verified = isinstance(payload, dict)
        if verified:
            try:
                verified = payload_sha256(payload) == row.payload_sha256
            except ValueError:
                verified = False
        safe_payload = cls._admin_safe(payload) if verified else None
        style_profile_version = (
            safe_payload.get("style_profile_version")
            if isinstance(safe_payload, dict)
            and isinstance(safe_payload.get("style_profile_version"), str)
            else None
        )
        has_response_body = row.artifact_type.lower() in {
            "styledresponse",
            "styled_response",
            "neutral_response",
        }
        if isinstance(safe_payload, dict) and has_response_body:
            safe_fields = {
                "preserved_citation_refs",
                "preserved_risk_flags",
                "schema_version",
                "style_profile_version",
                "used_action_ids",
                "used_block_ids",
                "used_claim_ids",
            }
            safe_payload = {key: value for key, value in safe_payload.items() if key in safe_fields}
        if isinstance(safe_payload, dict) and row.artifact_type.lower() == "response_plan":
            blocks = safe_payload.get("content_blocks")
            safe_payload = {
                key: value
                for key, value in safe_payload.items()
                if key
                in {
                    "schema_version",
                    "communication_act",
                    "requested_detail",
                    "citation_refs",
                    "prohibited_transformations",
                }
            }
            safe_payload["content_blocks"] = (
                [
                    {
                        key: value
                        for key, value in block.items()
                        if key in {"block_id", "kind", "source_refs", "required"}
                    }
                    for block in blocks
                    if isinstance(block, dict)
                ]
                if isinstance(blocks, list)
                else []
            )
        if isinstance(safe_payload, dict) and row.artifact_type.lower() == "directive":
            safe_payload = {
                key: value
                for key, value in safe_payload.items()
                if key
                in {
                    "schema_version",
                    "response_path",
                    "interaction_kind",
                    "evidence_refs",
                    "voice_act",
                    "requested_detail",
                }
            }
        if isinstance(safe_payload, dict):
            safe_payload = cls._professional_artifact_payload(
                artifact_type=row.artifact_type,
                payload=safe_payload,
            )
        professional_body_redacted = cls._normalized_artifact_type(row.artifact_type) in {
            "confirmeddishset",
            "dietguidanceassessment",
            "dishevidencebundle",
            "dishrecognition",
            "dishrecognitioncorrection",
            "evidencepacket",
            "nutritionobservations",
            "nutritionobservation",
            "professionalassessment",
            "conservativeassessment",
            "reviewerverdict",
        }
        return {
            "artifact_id": row.id,
            "invocation_id": row.invocation_id,
            "artifact_type": row.artifact_type,
            "producer_role": row.producer_role,
            "schema_version": row.schema_version,
            "parent_artifact_ids": parents,
            "payload_sha256": row.payload_sha256,
            "payload": safe_payload,
            "style_profile_version": style_profile_version,
            "body_redacted": has_response_body or professional_body_redacted,
            "integrity_status": "verified" if verified else "mismatch",
            "created_at": row.created_at,
        }

    @classmethod
    def _professional_artifact_payload(
        cls,
        *,
        artifact_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = cls._normalized_artifact_type(artifact_type)
        if normalized == "dishrecognition":
            return cls._safe_dish_recognition(payload)
        if normalized == "confirmeddishset":
            return cls._safe_confirmed_dish_set(payload)
        if normalized == "dishevidencebundle":
            return cls._safe_dish_evidence_bundle(payload)
        if normalized == "dietguidanceassessment":
            return cls._safe_diet_guidance_assessment(payload)
        if normalized == "dishrecognitioncorrection":
            return cls._safe_dish_recognition_correction(payload)
        if normalized == "evidencepacket":
            return cls._safe_evidence_packet(payload)
        if normalized in {"nutritionobservations", "nutritionobservation"}:
            return cls._safe_nutrition_observations(payload)
        if normalized in {"professionalassessment", "conservativeassessment"}:
            return cls._safe_professional_assessment(payload)
        if normalized == "reviewerverdict":
            return cls._safe_reviewer_verdict(payload)
        return payload

    @classmethod
    def _safe_dish_recognition(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_dishes = payload.get("dishes")
        dishes: list[dict[str, Any]] = []
        if isinstance(raw_dishes, list):
            for dish in raw_dishes:
                if not isinstance(dish, dict):
                    continue
                raw_candidates = dish.get("candidates")
                candidates = (
                    [
                        {
                            "label": candidate.get("label"),
                            "confidence": candidate.get("confidence"),
                        }
                        for candidate in raw_candidates
                        if isinstance(candidate, dict)
                    ]
                    if isinstance(raw_candidates, list)
                    else []
                )
                dishes.append(
                    {
                        "dish_ref": dish.get("dish_ref"),
                        "candidates": candidates,
                        "visible_ingredients": cls._safe_string_values(
                            dish.get("visible_ingredients")
                        ),
                        "preparation_candidates": cls._safe_string_values(
                            dish.get("preparation_candidates")
                        ),
                        "uncertainty_reasons": cls._safe_string_values(
                            dish.get("uncertainty_reasons")
                        ),
                        "requires_confirmation": bool(dish.get("requires_confirmation")),
                    }
                )
        return {
            "schema_version": payload.get("schema_version"),
            "asset_id": payload.get("asset_id"),
            "model": payload.get("model"),
            "prompt_version": payload.get("prompt_version"),
            "policy_version": payload.get("policy_version"),
            "image_kind": payload.get("image_kind"),
            "quality_flags": cls._safe_string_values(payload.get("quality_flags")),
            "dishes": dishes,
            "suggested_question_present": bool(payload.get("suggested_question")),
            "overall_requires_confirmation": bool(payload.get("overall_requires_confirmation")),
        }

    @classmethod
    def _safe_confirmed_dish_set(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_dishes = payload.get("dishes")
        return {
            "schema_version": payload.get("schema_version"),
            "source_artifact_id": payload.get("source_artifact_id"),
            "dishes": [
                {
                    "dish_ref": item.get("dish_ref"),
                    "name": item.get("name"),
                    "source": item.get("source"),
                    "recognition_confidence": item.get("recognition_confidence"),
                    "user_evidence_present": bool(item.get("user_evidence_ref")),
                }
                for item in raw_dishes
                if isinstance(item, dict)
            ]
            if isinstance(raw_dishes, list)
            else [],
        }

    @classmethod
    def _safe_dish_evidence_bundle(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_dishes = payload.get("dishes")
        dishes: list[dict[str, Any]] = []
        if isinstance(raw_dishes, list):
            for item in raw_dishes:
                if not isinstance(item, dict):
                    continue
                match = item.get("entity_match")
                rules = item.get("rules")
                citations = item.get("citations")
                dishes.append(
                    {
                        "dish_ref": item.get("dish_ref"),
                        "entity_match": {
                            key: match.get(key)
                            for key in (
                                "status",
                                "query_name",
                                "dish_entity_id",
                                "canonical_name",
                                "source_version",
                                "candidate_entity_ids",
                            )
                        }
                        if isinstance(match, dict)
                        else None,
                        "rules": [
                            {
                                key: rule.get(key)
                                for key in (
                                    "rule_id",
                                    "condition_type",
                                    "effect",
                                    "applicability",
                                    "source_refs",
                                    "user_constraint_refs",
                                )
                            }
                            for rule in rules
                            if isinstance(rule, dict)
                        ]
                        if isinstance(rules, list)
                        else [],
                        "rag_evidence_count": len(item.get("rag_evidence", []))
                        if isinstance(item.get("rag_evidence"), list)
                        else 0,
                        "citations": [
                            {
                                key: citation.get(key)
                                for key in (
                                    "citation_id",
                                    "source_id",
                                    "chunk_id",
                                    "title",
                                    "publisher",
                                    "version",
                                    "applicability",
                                    "review_status",
                                    "active",
                                    "content_sha256",
                                )
                            }
                            for citation in citations
                            if isinstance(citation, dict)
                        ]
                        if isinstance(citations, list)
                        else [],
                        "missing_information": cls._safe_string_values(
                            item.get("missing_information")
                        ),
                    }
                )
        return {
            "schema_version": payload.get("schema_version"),
            "corpus_status": payload.get("corpus_status"),
            "retrieval_receipt_ids": cls._safe_string_values(payload.get("retrieval_receipt_ids")),
            "dishes": dishes,
        }

    @classmethod
    def _safe_diet_guidance_assessment(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_dishes = payload.get("dishes")
        return {
            "schema_version": payload.get("schema_version"),
            "scope": payload.get("scope"),
            "dishes": [
                {
                    "dish_ref": item.get("dish_ref"),
                    "canonical_name": item.get("canonical_name"),
                    "suitability": item.get("suitability"),
                    "reason_count": len(item.get("reasons", []))
                    if isinstance(item.get("reasons"), list)
                    else 0,
                    "action_count": len(item.get("actions", []))
                    if isinstance(item.get("actions"), list)
                    else 0,
                    "hard_rule_refs": cls._safe_string_values(item.get("hard_rule_refs")),
                    "user_constraint_refs": cls._safe_string_values(
                        item.get("user_constraint_refs")
                    ),
                    "uncertainty_present": bool(item.get("uncertainty_note")),
                }
                for item in raw_dishes
                if isinstance(item, dict)
            ]
            if isinstance(raw_dishes, list)
            else [],
            "question_count": len(payload.get("questions", []))
            if isinstance(payload.get("questions"), list)
            else 0,
            "risk_flags": cls._safe_string_values(payload.get("risk_flags")),
            "referral_present": bool(payload.get("referral")),
        }

    @classmethod
    def _safe_dish_recognition_correction(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_dishes = payload.get("corrected_dishes")
        return {
            "schema_version": payload.get("schema_version"),
            "recognition_artifact_id": payload.get("recognition_artifact_id"),
            "corrected_dishes": [
                {
                    "dish_ref": item.get("dish_ref"),
                    "corrected_name": item.get("corrected_name"),
                }
                for item in raw_dishes
                if isinstance(item, dict)
            ]
            if isinstance(raw_dishes, list)
            else [],
            "reviewer": payload.get("reviewer"),
            "comment_present": bool(payload.get("comment")),
        }

    @classmethod
    def _safe_reviewer_verdict(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_issues = payload.get("issues")
        if isinstance(raw_issues, (list, tuple)):
            issues = [cls._safe_reviewer_issue(issue) for issue in raw_issues]
        elif isinstance(raw_issues, (dict, str)):
            issues = [cls._safe_reviewer_issue(raw_issues)]
        else:
            issues = []
        issue_types = cls._review_issue_types(payload)
        reviewed_ids = cls._safe_string_values(
            payload.get("reviewed_artifact_ids") or payload.get("input_artifact_ids")
        )
        candidate_id = payload.get("candidate_artifact_id")
        if isinstance(candidate_id, str) and candidate_id not in reviewed_ids:
            reviewed_ids.append(candidate_id)
        repair_attempt = payload.get("repair_attempt")
        repair_budget = payload.get("repair_budget")
        safe_repair_budget = (
            {
                str(key): value
                for key, value in repair_budget.items()
                if str(key).startswith("max_")
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            if isinstance(repair_budget, dict)
            else None
        )
        return {
            "schema_version": payload.get("schema_version"),
            "verdict": cls._first_string(
                payload.get("verdict"),
                payload.get("status"),
                payload.get("outcome"),
            ),
            "repair_target": cls._first_string(
                payload.get("repair_target"),
                payload.get("target"),
                payload.get("target_agent"),
            ),
            "issue_type": cls._first_string(
                payload.get("issue_type"),
                issue_types[0] if issue_types else None,
            ),
            "issue_types": issue_types,
            "issue_count": max(len(issues), len(issue_types)),
            "issues": issues,
            "reason_summary_present": bool(
                payload.get("reason_summary") or payload.get("reason") or payload.get("explanation")
            ),
            "reviewed_artifact_ids": reviewed_ids,
            "repair_attempt": (
                repair_attempt
                if isinstance(repair_attempt, int)
                and not isinstance(repair_attempt, bool)
                and repair_attempt >= 0
                else None
            ),
            "repair_budget": safe_repair_budget,
        }

    @classmethod
    def _safe_reviewer_issue(cls, issue: Any) -> dict[str, Any]:
        if isinstance(issue, str):
            return {
                "type": issue,
                "excerpt_present": False,
                "explanation_present": False,
            }
        if not isinstance(issue, dict):
            return {
                "type": None,
                "excerpt_present": False,
                "explanation_present": False,
            }
        return {
            "type": cls._first_string(
                issue.get("type"),
                issue.get("issue_type"),
                issue.get("code"),
                issue.get("category"),
            ),
            "excerpt_present": bool(issue.get("excerpt")),
            "explanation_present": bool(
                issue.get("explanation") or issue.get("reason") or issue.get("message")
            ),
        }

    @classmethod
    def _review_issue_types(cls, payload: dict[str, Any]) -> list[str]:
        values: list[str] = []
        primary = payload.get("issue_type")
        if isinstance(primary, str):
            values.append(primary)
        for key in ("issue_types", "issue_codes"):
            values.extend(cls._safe_string_values(payload.get(key)))
        issues = payload.get("issues")
        if isinstance(issues, (list, tuple)):
            for issue in issues:
                if isinstance(issue, str):
                    values.append(issue)
                    continue
                if not isinstance(issue, dict):
                    continue
                issue_type = cls._first_string(
                    issue.get("type"),
                    issue.get("issue_type"),
                    issue.get("code"),
                    issue.get("category"),
                )
                if issue_type is not None:
                    values.append(issue_type)
        elif isinstance(issues, str):
            values.append(issues)
        elif isinstance(issues, dict):
            issue_type = cls._safe_reviewer_issue(issues)["type"]
            if isinstance(issue_type, str):
                values.append(issue_type)
        return list(dict.fromkeys(values))

    @classmethod
    def _safe_evidence_packet(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_items = payload.get("items", payload.get("evidence", []))
        return {
            "schema_version": payload.get("schema_version"),
            "turn_id": payload.get("turn_id"),
            # Questions and user text remain available to the invoked agent but do
            # not need to be echoed into an administrative timeline.
            "user_request": None,
            "professional_question": None,
            "items": [cls._safe_evidence_item(item) for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else [],
            "missing_information": cls._safe_string_values(payload.get("missing_information")),
        }

    @classmethod
    def _safe_evidence_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        content = item.get("content")
        content_fields = (
            sorted(
                str(key)
                for key in content
                if str(key).lower()
                not in {
                    "chain_of_thought",
                    "developer_prompt",
                    "hidden_reasoning",
                    "messages",
                    "prompt",
                    "reasoning",
                    "reasoning_content",
                    "system_prompt",
                }
            )[:32]
            if isinstance(content, dict)
            else []
        )
        # Keeping null-valued keys lets the UI explain which evidence dimensions
        # were present without disclosing user text, notes, food names, or metrics.
        return {
            "evidence_id": item.get("evidence_id") or item.get("id"),
            "source_type": item.get("source_type") or item.get("kind") or item.get("type"),
            "authority": item.get("authority"),
            "occurred_at": item.get("occurred_at"),
            "content": {key: None for key in content_fields},
            "confidence": item.get("confidence"),
            "uncertainty": "present" if item.get("uncertainty") else None,
            "source_ref": item.get("source_ref"),
        }

    @classmethod
    def _safe_nutrition_observations(cls, payload: dict[str, Any]) -> dict[str, Any]:
        raw_evidence = payload.get("evidence", payload.get("items", []))
        raw_calculations = payload.get(
            "calculations",
            payload.get(
                "calculation_results",
                payload.get("calculation_observations", payload.get("observations", [])),
            ),
        )
        knowledge = payload.get("knowledge", payload.get("knowledge_status"))
        return {
            "schema_version": payload.get("schema_version"),
            "rag_enabled": (
                payload.get("rag_enabled") if isinstance(payload.get("rag_enabled"), bool) else None
            ),
            "evidence": [
                cls._safe_evidence_item(item) for item in raw_evidence if isinstance(item, dict)
            ]
            if isinstance(raw_evidence, list)
            else [],
            "calculations": [
                cls._safe_calculation(item) for item in raw_calculations if isinstance(item, dict)
            ]
            if isinstance(raw_calculations, list)
            else [],
            "knowledge": cls._safe_knowledge(knowledge),
        }

    @staticmethod
    def _safe_calculation(item: dict[str, Any]) -> dict[str, Any]:
        inputs = item.get("inputs")
        return {
            "observation_id": item.get("observation_id") or item.get("evidence_id"),
            "calculation_type": item.get("calculation_type") or item.get("tool_name"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "inputs": (
                {str(key): None for key in sorted(inputs, key=str)[:32]}
                if isinstance(inputs, dict)
                else {}
            ),
            "evidence_refs": AdminQueryRepository._safe_string_values(
                item.get("evidence_refs") or item.get("source_ids")
            ),
        }

    @classmethod
    def _safe_professional_assessment(cls, payload: dict[str, Any]) -> dict[str, Any]:
        findings = payload.get("findings", payload.get("claims", []))
        actions = payload.get("actions", [])
        citations = payload.get("citations", [])
        return {
            "schema_version": payload.get("schema_version"),
            "assessment_type": payload.get("assessment_type"),
            "overall": None,
            "findings": [
                {
                    "claim_id": finding.get("claim_id"),
                    "category": finding.get("category"),
                    "statement": None,
                    "basis_types": cls._safe_string_values(finding.get("basis_types")),
                    "evidence_refs": cls._safe_string_values(finding.get("evidence_refs")),
                    "knowledge_refs": cls._safe_string_values(finding.get("knowledge_refs")),
                    "confidence": finding.get("confidence"),
                }
                for finding in findings
                if isinstance(finding, dict)
            ]
            if isinstance(findings, list)
            else [],
            "priority_problem": None,
            "actions": [
                {
                    "action_id": action.get("action_id"),
                    "statement": None,
                    "basis_claim_ids": cls._safe_string_values(action.get("basis_claim_ids")),
                }
                for action in actions
                if isinstance(action, dict)
            ]
            if isinstance(actions, list)
            else [],
            "questions": [],
            "risk_flags": cls._safe_string_values(payload.get("risk_flags")),
            "uncertainty_note": "present" if payload.get("uncertainty_note") else None,
            "citations": [
                cls._safe_citation(citation) for citation in citations if isinstance(citation, dict)
            ]
            if isinstance(citations, list)
            else [],
        }

    @classmethod
    def _safe_citation(cls, citation: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": citation.get("rank"),
            "candidate_id": citation.get("candidate_id"),
            "citation_id": citation.get("citation_id"),
            "source_id": citation.get("source_id"),
            "chunk_id": citation.get("chunk_id"),
            "title": citation.get("title"),
            "publisher": citation.get("publisher"),
            "published_at": citation.get("published_at"),
            "version": citation.get("version"),
            "section_or_page": citation.get("section_or_page"),
            "source_url": citation.get("source_url"),
            "applicability": cls._safe_string_values(citation.get("applicability")),
            "review_status": citation.get("review_status"),
            "publication_status": citation.get("publication_status"),
            "retrieved_in_invocation_id": citation.get("retrieved_in_invocation_id"),
            "keyword_score": citation.get(
                "keyword_score",
                citation.get("lexical_score"),
            ),
            "lexical_score": citation.get(
                "lexical_score",
                citation.get("keyword_score"),
            ),
            "vector_score": citation.get("vector_score"),
            "rerank_score": citation.get("rerank_score"),
            "match_reasons": cls._safe_string_values(citation.get("match_reasons")),
            "adoption_status": citation.get("adoption_status"),
            "active": citation.get("active"),
            "content_sha256": citation.get("content_sha256"),
            "source_content_sha256": citation.get("source_content_sha256"),
            # Knowledge snippets can contain sensitive query-adjacent material and
            # are not required for the default administrative timeline.
            "excerpt": None,
            "snippet": None,
            "content": None,
        }

    @classmethod
    def _safe_knowledge(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        citations = value.get("citations", [])
        candidates = value.get(
            "candidates",
            value.get("candidate_citations", value.get("retrieved_candidates", [])),
        )
        adopted = value.get("adopted_citations", value.get("final_citations", []))
        return {
            "corpus_status": value.get("corpus_status", value.get("status")),
            "citations": [
                cls._safe_citation(citation) for citation in citations if isinstance(citation, dict)
            ]
            if isinstance(citations, list)
            else [],
            "candidates": [
                cls._safe_citation(candidate)
                for candidate in candidates
                if isinstance(candidate, dict)
            ]
            if isinstance(candidates, list)
            else [],
            "adopted_citations": [
                cls._safe_citation(citation) for citation in adopted if isinstance(citation, dict)
            ]
            if isinstance(adopted, list)
            else [],
            # Query wording can contain sensitive user context.  The status and
            # citation identities are sufficient for audit.
            "query_summary": None,
        }

    @classmethod
    def _evidence_summary(cls, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        packet = cls._latest_artifact(artifacts, {"evidencepacket"})
        observations = cls._latest_artifact(
            artifacts,
            {"nutritionobservations", "nutritionobservation"},
        )
        assessment = cls._latest_artifact(
            artifacts,
            {"professionalassessment", "conservativeassessment"},
        )
        packet_payload = packet.get("payload") if packet is not None else None
        observation_payload = observations.get("payload") if observations is not None else None
        assessment_payload = assessment.get("payload") if assessment is not None else None
        packet_payload = packet_payload if isinstance(packet_payload, dict) else {}
        observation_payload = observation_payload if isinstance(observation_payload, dict) else {}
        assessment_payload = assessment_payload if isinstance(assessment_payload, dict) else {}

        items: list[dict[str, Any]] = []
        for raw_items in (
            packet_payload.get("items"),
            observation_payload.get("evidence"),
        ):
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if isinstance(item, dict):
                    items.append(item)
        calculations = observation_payload.get("calculations")
        calculations = calculations if isinstance(calculations, list) else []
        deduplicated_items: dict[str, dict[str, Any]] = {}
        for item in items:
            item_id = item.get("evidence_id")
            if isinstance(item_id, str):
                deduplicated_items[item_id] = item
        known_evidence = set(deduplicated_items)
        for calculation in calculations:
            if isinstance(calculation, dict):
                observation_id = calculation.get("observation_id")
                if isinstance(observation_id, str):
                    known_evidence.add(observation_id)

        claims = assessment_payload.get("findings")
        claims = claims if isinstance(claims, list) else []
        actions = assessment_payload.get("actions")
        actions = actions if isinstance(actions, list) else []
        citations = assessment_payload.get("citations")
        citations = citations if isinstance(citations, list) else []
        known_knowledge = {
            value
            for citation in citations
            if isinstance(citation, dict)
            for value in (
                citation.get("citation_id"),
                citation.get("source_id"),
                citation.get("chunk_id"),
            )
            if isinstance(value, str)
        }
        known_claims = {
            claim.get("claim_id")
            for claim in claims
            if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
        }
        unresolved_evidence = sorted(
            {
                ref
                for claim in claims
                if isinstance(claim, dict)
                for ref in cls._safe_string_values(claim.get("evidence_refs"))
                if ref not in known_evidence
            }
        )
        unresolved_knowledge = sorted(
            {
                ref
                for claim in claims
                if isinstance(claim, dict)
                for ref in cls._safe_string_values(claim.get("knowledge_refs"))
                if ref not in known_knowledge
            }
        )
        unresolved_claims = sorted(
            {
                ref
                for action in actions
                if isinstance(action, dict)
                for ref in cls._safe_string_values(action.get("basis_claim_ids"))
                if ref not in known_claims
            }
        )
        knowledge = observation_payload.get("knowledge")
        if not isinstance(knowledge, dict):
            knowledge = None
        return {
            "packet_artifact_id": packet.get("artifact_id") if packet else None,
            "observations_artifact_id": observations.get("artifact_id") if observations else None,
            "assessment_artifact_id": assessment.get("artifact_id") if assessment else None,
            "items": list(deduplicated_items.values()),
            "calculations": [item for item in calculations if isinstance(item, dict)],
            "missing_information": cls._safe_string_values(
                packet_payload.get("missing_information")
            ),
            "claims": [item for item in claims if isinstance(item, dict)],
            "actions": [item for item in actions if isinstance(item, dict)],
            "knowledge": knowledge,
            "adopted_citations": [
                cls._safe_citation(item) for item in citations if isinstance(item, dict)
            ],
            "unresolved_evidence_refs": unresolved_evidence,
            "unresolved_knowledge_refs": unresolved_knowledge,
            "unresolved_claim_refs": unresolved_claims,
            "visual_uncertainty_count": sum(
                1
                for item in deduplicated_items.values()
                if item.get("source_type") == "vision_observation"
                and item.get("uncertainty") is not None
            ),
        }

    @classmethod
    def _latest_artifact(
        cls,
        artifacts: list[dict[str, Any]],
        artifact_types: set[str],
    ) -> dict[str, Any] | None:
        return next(
            (
                artifact
                for artifact in reversed(artifacts)
                if cls._normalized_artifact_type(str(artifact.get("artifact_type", "")))
                in artifact_types
            ),
            None,
        )

    @staticmethod
    def _normalized_artifact_type(value: str) -> str:
        return "".join(character for character in value.lower() if character.isalnum())

    @staticmethod
    def _safe_string_values(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _first_string(*values: Any) -> str | None:
        return next(
            (value for value in values if isinstance(value, str) and value),
            None,
        )

    @classmethod
    def _review_summary(
        cls,
        *,
        artifacts: list[dict[str, Any]],
        invocations: list[dict[str, Any]],
        transitions: list[dict[str, Any]],
        adopted: dict[str, Any],
        degraded_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        artifact_by_id = {
            artifact["artifact_id"]: artifact
            for artifact in artifacts
            if isinstance(artifact.get("artifact_id"), str)
        }
        invocation_by_id = {
            invocation["invocation_id"]: invocation
            for invocation in invocations
            if isinstance(invocation.get("invocation_id"), str)
        }
        reviewer_invocations = [
            invocation
            for invocation in invocations
            if str(invocation.get("agent_role", "")).lower() in {"response_reviewer", "reviewer"}
        ]
        verdict_artifacts = [
            artifact
            for artifact in artifacts
            if cls._normalized_artifact_type(str(artifact.get("artifact_type", "")))
            == "reviewerverdict"
        ]
        verdicts: list[dict[str, Any]] = []
        for artifact in verdict_artifacts:
            payload = artifact.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            parents = cls._safe_string_values(artifact.get("parent_artifact_ids"))
            reviewed_ids = cls._safe_string_values(payload.get("reviewed_artifact_ids"))
            for parent in parents:
                if parent not in reviewed_ids:
                    reviewed_ids.append(parent)
            verdict_status = cls._first_string(
                payload.get("verdict"),
                payload.get("status"),
                payload.get("outcome"),
            )
            repair_target = cls._first_string(
                payload.get("repair_target"),
                payload.get("target"),
                payload.get("target_agent"),
            )
            issue_types = cls._review_issue_types(payload)
            issues = payload.get("issues")
            issues = (
                [issue for issue in issues if isinstance(issue, dict)]
                if isinstance(issues, list)
                else []
            )
            issue_count = payload.get("issue_count")
            invocation = invocation_by_id.get(artifact.get("invocation_id"))
            verdicts.append(
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "invocation_id": artifact.get("invocation_id"),
                    "verdict": verdict_status,
                    "repair_target": repair_target,
                    "issue_type": cls._first_string(
                        payload.get("issue_type"),
                        issue_types[0] if issue_types else None,
                    ),
                    "issue_types": issue_types,
                    "issue_count": (
                        issue_count
                        if isinstance(issue_count, int) and not isinstance(issue_count, bool)
                        else max(len(issue_types), len(issues))
                    ),
                    "issues": issues,
                    "reason_summary_present": bool(payload.get("reason_summary_present")),
                    "reviewed_artifact_ids": reviewed_ids,
                    "parent_artifact_ids": parents,
                    "attempt": invocation.get("attempt") if invocation else None,
                    "repair_attempt": payload.get("repair_attempt"),
                    "repair_budget": payload.get("repair_budget"),
                    "created_at": artifact.get("created_at"),
                    "integrity_status": artifact.get("integrity_status"),
                }
            )

        repair_transitions = [
            transition for transition in transitions if cls._is_review_repair_transition(transition)
        ]
        repair_verdicts = [verdict for verdict in verdicts if verdict.get("verdict") == "repair"]
        repair_invocations = [
            invocation
            for invocation in invocations
            if (
                int(invocation.get("attempt") or 0) > 1
                or "repair" in str(invocation.get("reason_summary", "")).lower()
            )
            and str(invocation.get("agent_role", "")).lower()
            in {"orchestrator", "nutrition_expert", "response_style"}
        ]
        used_invocation_ids: set[str] = set()
        repair_attempts: list[dict[str, Any]] = []
        for index, transition in enumerate(repair_transitions):
            verdict = repair_verdicts[index] if index < len(repair_verdicts) else None
            target = verdict.get("repair_target") if verdict is not None else None
            if not isinstance(target, str) or not target:
                target = cls._repair_target_from_transition(transition)
            invocation = next(
                (
                    candidate
                    for candidate in repair_invocations
                    if candidate.get("invocation_id") not in used_invocation_ids
                    and (target is None or str(candidate.get("agent_role", "")).lower() == target)
                ),
                None,
            )
            if invocation is not None and isinstance(invocation.get("invocation_id"), str):
                used_invocation_ids.add(invocation["invocation_id"])
            reviewed_ids = (
                cls._safe_string_values(verdict.get("reviewed_artifact_ids"))
                if verdict is not None
                else []
            )
            repair_attempts.append(
                {
                    "attempt": (
                        transition.get("attempt")
                        or (invocation.get("attempt") if invocation is not None else None)
                        or index + 1
                    ),
                    "target": target or "unknown",
                    "verdict_artifact_id": (
                        verdict.get("artifact_id") if verdict is not None else None
                    ),
                    "input_artifact_id": reviewed_ids[0] if reviewed_ids else None,
                    "output_artifact_id": (
                        invocation.get("output_artifact_id") if invocation is not None else None
                    ),
                    "status": (
                        invocation.get("status") if invocation is not None else "transitioned"
                    ),
                    "transition": dict(transition),
                }
            )

        target_names = ("orchestrator", "nutrition_expert", "response_style")
        repair_counts = {
            target: sum(1 for attempt in repair_attempts if attempt["target"] == target)
            for target in target_names
        }
        repair_counts["unknown"] = sum(
            1 for attempt in repair_attempts if attempt["target"] not in target_names
        )
        repair_counts["total"] = len(repair_attempts)
        budget_transitions = [
            transition for transition in transitions if cls._is_review_budget_transition(transition)
        ]
        exhausted_targets = list(
            dict.fromkeys(
                target
                for transition in budget_transitions
                if (target := cls._repair_target_from_transition(transition)) is not None
            )
        )
        if budget_transitions and not exhausted_targets and repair_verdicts:
            latest_repair_target = repair_verdicts[-1].get("repair_target")
            if isinstance(latest_repair_target, str):
                exhausted_targets.append(latest_repair_target)
        configured_limits = next(
            (
                verdict["repair_budget"]
                for verdict in reversed(verdicts)
                if isinstance(verdict.get("repair_budget"), dict)
            ),
            None,
        )

        original_id = cls._original_reviewed_artifact_id(
            verdicts=verdicts,
            reviewer_invocations=reviewer_invocations,
            artifact_by_id=artifact_by_id,
        )
        repaired_ids: list[str] = []
        for attempt in repair_attempts:
            artifact_id = attempt.get("output_artifact_id")
            if (
                isinstance(artifact_id, str)
                and artifact_id != original_id
                and artifact_id not in repaired_ids
            ):
                repaired_ids.append(artifact_id)
        for verdict in verdicts[1:]:
            for artifact_id in cls._safe_string_values(verdict.get("reviewed_artifact_ids")):
                if (
                    artifact_id != original_id
                    and artifact_id not in repaired_ids
                    and cls._normalized_artifact_type(
                        str(artifact_by_id.get(artifact_id, {}).get("artifact_type", ""))
                    )
                    != "reviewerverdict"
                ):
                    repaired_ids.append(artifact_id)
        adopted_id = adopted.get("artifact_id") if adopted.get("final") is True else None
        adopted_id = adopted_id if isinstance(adopted_id, str) else None
        comparison = {
            "original": cls._review_artifact_ref(
                artifact_by_id.get(original_id),
                artifact_id=original_id,
            ),
            "repaired": [
                cls._review_artifact_ref(
                    artifact_by_id.get(artifact_id),
                    artifact_id=artifact_id,
                )
                for artifact_id in repaired_ids
            ],
            "final_adopted": cls._review_artifact_ref(
                artifact_by_id.get(adopted_id),
                artifact_id=adopted_id,
            ),
            "changed": (
                original_id != adopted_id
                if original_id is not None and adopted_id is not None
                else None
            ),
        }
        latest_verdict = verdicts[-1].get("verdict") if verdicts else None
        rejected = latest_verdict == "reject" or any(
            transition.get("reason_code") == "review_rejected" for transition in transitions
        )
        review_degraded_events = [
            event
            for event in degraded_events
            if isinstance(event.get("details"), dict)
            and (
                "review" in str(event["details"].get("reason_code", "")).lower()
                or "review" in str(event["details"].get("fallback_type", "")).lower()
            )
        ]
        degraded = rejected or bool(budget_transitions) or bool(review_degraded_events)
        if rejected:
            status = "rejected"
        elif degraded:
            status = "degraded"
        elif latest_verdict == "pass":
            status = "passed"
        elif latest_verdict == "repair":
            status = "repair_requested"
        elif any(item.get("status") == "started" for item in reviewer_invocations):
            status = "running"
        elif reviewer_invocations or verdicts:
            status = "completed_unknown"
        else:
            status = "not_run"
        return {
            "status": status,
            "reviewer_invocation_count": len(reviewer_invocations),
            "verdict_count": len(verdicts),
            "issue_count": sum(int(verdict["issue_count"]) for verdict in verdicts),
            "verdicts": verdicts,
            "repair_attempts": repair_attempts,
            "repair_counts": repair_counts,
            "budget": {
                "exhausted": bool(budget_transitions),
                "exhausted_targets": exhausted_targets,
                "repair_attempts_observed": len(repair_attempts),
                "configured_limits": configured_limits,
            },
            "comparison": comparison,
            "rejected": rejected,
            "degraded": degraded,
        }

    @classmethod
    def _original_reviewed_artifact_id(
        cls,
        *,
        verdicts: list[dict[str, Any]],
        reviewer_invocations: list[dict[str, Any]],
        artifact_by_id: dict[str, dict[str, Any]],
    ) -> str | None:
        candidates = (
            cls._safe_string_values(verdicts[0].get("reviewed_artifact_ids")) if verdicts else []
        )
        if not candidates and reviewer_invocations:
            candidates = cls._safe_string_values(reviewer_invocations[0].get("input_artifact_ids"))
        response_types = {
            "styledresponse",
            "neutralresponse",
            "responseplan",
        }
        return next(
            (
                artifact_id
                for artifact_id in candidates
                if cls._normalized_artifact_type(
                    str(artifact_by_id.get(artifact_id, {}).get("artifact_type", ""))
                )
                in response_types
            ),
            candidates[0] if candidates else None,
        )

    @classmethod
    def _review_artifact_ref(
        cls,
        artifact: dict[str, Any] | None,
        *,
        artifact_id: str | None,
    ) -> dict[str, Any] | None:
        if artifact_id is None:
            return None
        artifact = artifact or {}
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact.get("artifact_type"),
            "producer_role": artifact.get("producer_role"),
            "schema_version": artifact.get("schema_version"),
            "payload_sha256": artifact.get("payload_sha256"),
            "integrity_status": artifact.get("integrity_status"),
            "invocation_id": artifact.get("invocation_id"),
            "created_at": artifact.get("created_at"),
        }

    @staticmethod
    def _is_review_repair_transition(transition: dict[str, Any]) -> bool:
        reason = str(transition.get("reason_code", "")).lower()
        source = str(transition.get("from_node", "")).lower()
        return reason == "review_repair" or (
            source in {"review", "review_running", "response_reviewer"}
            and reason in {"repair", "retry", "return_for_repair"}
        )

    @staticmethod
    def _is_review_budget_transition(transition: dict[str, Any]) -> bool:
        return str(transition.get("reason_code", "")).lower() == "budget_exhausted" and str(
            transition.get("from_node", "")
        ).lower() in {"review", "review_running", "response_reviewer"}

    @classmethod
    def _repair_target_from_transition(
        cls,
        transition: dict[str, Any] | None,
    ) -> str | None:
        if transition is None:
            return None
        node = str(transition.get("to_node", "")).lower()
        if node in {"style", "style_running", "response_style"}:
            return "response_style"
        if node in {
            "expert_running",
            "nutrition",
            "nutrition_expert",
            "nutrition_running",
        }:
            return "nutrition_expert"
        if node in {"orchestrator", "orchestrator_running"}:
            return "orchestrator"
        if transition.get("reason_code") == "budget_exhausted":
            source = str(transition.get("from_node", "")).lower()
            if source in {"style", "style_running", "response_style"}:
                return "response_style"
            if source in {"expert_running", "nutrition", "nutrition_expert"}:
                return "nutrition_expert"
            if source in {"orchestrator", "orchestrator_running"}:
                return "orchestrator"
        return None

    @staticmethod
    def _style_summary(
        *,
        artifacts: list[dict[str, Any]],
        transitions: list[dict[str, Any]],
        adopted: dict[str, Any],
        degraded_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        style_artifacts = [
            artifact
            for artifact in artifacts
            if str(artifact.get("artifact_type", "")).lower()
            in {
                "style_resolution",
                "styledresponse",
                "styled_response",
                "neutral_response",
            }
        ]
        response_artifacts = [
            artifact
            for artifact in style_artifacts
            if str(artifact.get("artifact_type", "")).lower()
            in {"styledresponse", "styled_response", "neutral_response"}
        ]
        style_profile_version = next(
            (
                artifact.get("style_profile_version")
                for artifact in reversed(style_artifacts)
                if isinstance(artifact.get("style_profile_version"), str)
            ),
            None,
        )
        bypass_transition = next(
            (
                transition
                for transition in transitions
                if transition.get("reason_code") == "style_bypassed"
            ),
            None,
        )
        style_failure = next(
            (
                transition
                for transition in transitions
                if transition.get("reason_code") in {"style_failed", "budget_exhausted"}
                and transition.get("from_node") == "style_running"
            ),
            None,
        )
        style_degraded_events = [
            event
            for event in degraded_events
            if isinstance(event.get("details"), dict)
            and (
                "style" in str(event["details"].get("reason_code", "")).lower()
                or "neutral" in str(event["details"].get("fallback_type", "")).lower()
            )
        ]
        degraded_detail = (
            style_degraded_events[-1].get("details")
            if style_degraded_events and isinstance(style_degraded_events[-1].get("details"), dict)
            else {}
        )
        degraded_reason = (
            degraded_detail.get("reason_code") if isinstance(degraded_detail, dict) else None
        )
        adopted_id = adopted.get("artifact_id")
        adopted_style = next(
            (
                artifact
                for artifact in response_artifacts
                if artifact.get("artifact_id") == adopted_id
            ),
            None,
        )
        bypassed = bypass_transition is not None
        degraded = style_failure is not None or bool(style_degraded_events)
        adopted_flag = adopted_style is not None
        final = adopted.get("final") if isinstance(adopted.get("final"), bool) else None
        if bypassed:
            status = "bypassed"
        elif degraded:
            status = "degraded"
        elif adopted_flag and final is True:
            status = "adopted"
        elif adopted_flag:
            status = "shadow_candidate"
        elif response_artifacts:
            status = "generated"
        else:
            status = "not_run"
        return {
            "style_profile_version": style_profile_version,
            "status": status,
            "bypassed": bypassed,
            "bypass_reason": (
                bypass_transition.get("reason_code") if bypass_transition is not None else None
            ),
            "degraded": degraded,
            "degraded_reason": (
                degraded_reason
                or (style_failure.get("reason_code") if style_failure is not None else None)
            ),
            "adopted": adopted_flag,
            "adopted_artifact_id": (adopted_id if isinstance(adopted_id, str) else None),
            "adoption_mode": (
                adopted.get("mode") if isinstance(adopted.get("mode"), str) else None
            ),
            "final": final,
        }

    @classmethod
    def _shadow_comparison(
        cls,
        *,
        mode: str,
        adopted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        artifact_rows: tuple[AgentArtifactRecord, ...],
        invocations: list[dict[str, Any]],
        output: dict[str, Any] | None,
    ) -> dict[str, Any]:
        artifact_id = adopted.get("artifact_id")
        candidate_artifact = next(
            (artifact for artifact in artifacts if artifact["artifact_id"] == artifact_id),
            None,
        )
        candidate_status = next(
            (
                invocation["status"]
                for invocation in reversed(invocations)
                if invocation["output_artifact_id"] == artifact_id
            ),
            "generated" if candidate_artifact is not None else None,
        )
        candidate_row = next(
            (row for row in artifact_rows if row.id == artifact_id),
            None,
        )
        return {
            "mode": mode,
            "delivery_status": "not_sent",
            "business_writes": "no_business_writes",
            "legacy": {
                "artifact_id": None,
                "content": output.get("content") if output is not None else None,
                "status": output.get("status") if output is not None else None,
            },
            "candidate": {
                "artifact_id": artifact_id if isinstance(artifact_id, str) else None,
                "content": cls._stored_artifact_content(candidate_row),
                "status": candidate_status,
            },
        }

    @classmethod
    def _stored_artifact_content(
        cls,
        row: AgentArtifactRecord | None,
    ) -> str | None:
        if row is None:
            return None
        payload = cls._json_load(row.payload_json)
        if not isinstance(payload, dict):
            return None
        try:
            if payload_sha256(payload) != row.payload_sha256:
                return None
        except ValueError:
            return None
        return cls._artifact_content(payload)

    @staticmethod
    def _artifact_content(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("content", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        for key in ("response", "styled_response", "candidate"):
            value = payload.get(key)
            if isinstance(value, dict):
                for content_key in ("content", "text"):
                    content = value.get(content_key)
                    if isinstance(content, str):
                        return content
        return None

    @staticmethod
    def _workflow_events(
        timeline: list[dict[str, Any]],
        operation: str,
    ) -> list[dict[str, Any]]:
        return [
            event
            for event in timeline
            if event.get("event_type") == "agent_item" and event.get("operation") == operation
        ]

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @classmethod
    def _admin_safe(cls, value: Any) -> Any:
        blocked = {
            "chain_of_thought",
            "developer_prompt",
            "hidden_reasoning",
            "messages",
            "prompt",
            "prompts",
            "reasoning",
            "reasoning_content",
            "system_prompt",
            "thought",
            "thoughts",
        }
        if isinstance(value, dict):
            return {
                key: cls._admin_safe(item)
                for key, item in value.items()
                if str(key).lower() not in blocked
            }
        if isinstance(value, list):
            return [cls._admin_safe(item) for item in value]
        return value

    @classmethod
    def _duration_ms(
        cls,
        started_at: datetime | None,
        completed_at: datetime | None,
    ) -> int | None:
        if started_at is None or completed_at is None:
            return None
        return max(
            0,
            int((cls._aware(completed_at) - cls._aware(started_at)).total_seconds() * 1000),
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
                (cls._aware(span.completed_at) - cls._aware(span.started_at)).total_seconds() * 1000
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
        details = cls._admin_safe(cls._json_load(item.payload_json))
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
                int((cls._aware(completed_at) - cls._aware(started_at)).total_seconds() * 1000),
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
