"""Compose a reviewed doctor reply and independent operation notices."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from slim_guard.agents.coach import CoachResponder, respond
from slim_guard.group_chat.composer import OutgoingTextMessage, compose_messages
from slim_guard.group_chat.contracts import ParticipantRole
from slim_guard.group_chat.failures import reply_failure_notice
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.runtime.contracts import AgentArtifact, ArtifactProducerRole, InvocationStatus
from slim_guard.runtime.invocation import InvocationStore

from .coach_execution import RecordedCoachTurn
from .contracts import ResponseFinalizationRequest, ResponseFinalizationResult
from .evidence import build_evidence
from .notices import operation_notices
from .profiles import StyleProfileResolver


class AgentResponseFinalizer:
    def __init__(
        self,
        *,
        coach: CoachResponder,
        profile_resolver: StyleProfileResolver,
        persistence: InvocationStore,
        recorder: HarnessRunRecorder,
        graph_version: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._coach, self._profiles = coach, profile_resolver
        self._store, self._recorder, self._graph = persistence, recorder, graph_version
        self._clock = clock or (lambda: datetime.now(UTC))

    async def finalize(self, request: ResponseFinalizationRequest) -> ResponseFinalizationResult:
        evidence = build_evidence(request)
        facts = await self._artifact(
            request,
            ArtifactProducerRole.EVIDENCE_BUILDER,
            "coach_evidence",
            evidence.model_dump(mode="json"),
        )
        selection = await self._profiles.resolve()
        resolution = await self._artifact(
            request,
            ArtifactProducerRole.STYLE_RESOLVER,
            "style_resolution",
            {
                "profile_id": selection.snapshot.profile.profile_id,
                "profile_version": selection.snapshot.profile.version,
                "requested_version": selection.requested_version,
                "source": selection.source,
                "fallback_reason": selection.fallback_reason,
                "mode": "direct_generation",
                "example_ids": [e.example_id for e in selection.snapshot.examples],
            },
            (facts.artifact_id,),
        )
        turn = RecordedCoachTurn(
            coach=self._coach,
            request=request,
            evidence=evidence,
            style=selection.snapshot,
            store=self._store,
            recorder=self._recorder,
            graph=self._graph,
            clock=self._clock,
            parents=(facts.artifact_id, resolution.artifact_id),
        )
        result = await respond(turn)
        outgoing = (
            OutgoingTextMessage(
                participant=ParticipantRole.COACH,
                text=result.reply.text,
                source_refs=result.reply.source_refs,
                style_profile_version=selection.snapshot.profile.version,
            )
            if result.reply
            else reply_failure_notice()
        )
        notices = operation_notices(
            completion=request.completion,
            outcomes=request.tool_outcomes,
            allowed_source_refs=evidence.allowed_source_refs,
        )
        messages = compose_messages(
            turn_id=request.turn_id,
            text_messages=(outgoing, *notices),
            tool_outcomes=request.tool_outcomes,
            created_at=self._clock(),
        )
        status = InvocationStatus.DEGRADED if result.failure_code else InvocationStatus.SUCCEEDED
        final = await self._artifact(
            request,
            ArtifactProducerRole.COACH_RESPONSE,
            "outgoing_messages",
            {
                "messages": [m.model_dump(mode="json") for m in messages],
                "failure_code": result.failure_code,
                "status": status.value,
                "style_profile_version": selection.snapshot.profile.version,
                "style_advisory": result.style_advisory,
                "adopted_attempt": result.adopted_attempt,
            },
            turn.review_artifacts.get(result.adopted_attempt or 0, turn.parents),
        )
        await self._recorder.record_workflow_event(
            turn_id=request.turn_id,
            event_type=ItemType.RESPONSE_DEGRADED
            if result.failure_code
            else ItemType.RESPONSE_ADOPTED,
            payload={
                "artifact_id": final.artifact_id,
                "mode": "core_primary",
                "reason_code": result.failure_code,
                "fallback_type": "system_notice" if result.failure_code else None,
                "style_profile_version": selection.snapshot.profile.version,
                "style_advisory": result.style_advisory,
                "adopted_attempt": result.adopted_attempt,
                "final": True,
            },
        )
        return ResponseFinalizationResult(
            text="\n".join(m.text for m in messages if m.text),
            status=status,
            evidence_artifact_id=facts.artifact_id,
            final_output_artifact_id=final.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
            reviewer_ran=bool(result.checks),
            reply_repaired=result.repair_attempted,
            failure_code=result.failure_code,
            messages=messages,
        )

    async def _artifact(
        self,
        request: ResponseFinalizationRequest,
        producer: ArtifactProducerRole,
        kind: str,
        payload: dict[str, Any],
        parents: tuple[str, ...] = (),
    ) -> AgentArtifact:
        artifact = AgentArtifact.create(
            artifact_id=f"artifact-{uuid4()}",
            turn_id=request.turn_id,
            producer_role=producer,
            artifact_type=kind,
            schema_version="1",
            payload=payload,
            parent_artifact_ids=parents,
            created_at=self._clock(),
        )
        await self._store.append_artifact(artifact)
        return artifact
