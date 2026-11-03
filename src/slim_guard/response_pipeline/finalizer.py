"""Evidence -> direct styled coach reply -> grounding review, plus independent notices."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from slim_guard.agents.coach import COACH_PROMPT_VERSION, COACH_REVIEW_VERSION, CoachResponder
from slim_guard.group_chat.composer import OutgoingTextMessage, compose_messages
from slim_guard.group_chat.contracts import ParticipantRole
from slim_guard.group_chat.failures import reply_failure_notice
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
)
from slim_guard.response_pipeline.evidence import build_evidence
from slim_guard.response_pipeline.notices import operation_notices
from slim_guard.response_pipeline.profiles import StyleProfileResolver
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationRunResult, InvocationStore


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
        self._coach = coach
        self._profiles = profile_resolver
        self._store = persistence
        self._recorder = recorder
        self._graph = graph_version
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
        notices = operation_notices(
            completion=request.completion,
            outcomes=request.tool_outcomes,
            allowed_source_refs=evidence.allowed_source_refs,
        )
        feedback: tuple[dict[str, object], ...] = ()
        previous = None
        calls = tokens = 0
        reviewed = False
        outgoing = []
        failure: str | None = "coach_generation_failed"
        parents: tuple[str, ...] = (facts.artifact_id, resolution.artifact_id)
        for attempt in (1, 2):
            invocation = self._invocation(
                request, AgentRole.COACH_RESPONSE, COACH_PROMPT_VERSION, parents, attempt
            )
            await self._store.start_invocation(
                invocation, reason_summary="直接按医生风格回应用户", started_at=self._clock()
            )
            generated = await self._coach.generate(
                invocation=invocation,
                evidence=evidence,
                style=selection.snapshot,
                feedback=feedback,
                previous_reply=previous,
            )
            candidate = await self._stage(request, invocation, generated, "coach_reply", parents)
            calls += generated.model_call_count
            tokens += generated.total_token_count
            if generated.output is None:
                failure = generated.failure_code or "coach_generation_failed"
                # Schema repair already consumed its own bounded attempt.
                break
            previous = generated.output.text
            review_invocation = self._invocation(
                request,
                AgentRole.COACH_REVIEW,
                COACH_REVIEW_VERSION,
                (facts.artifact_id, resolution.artifact_id, candidate.artifact_id),
                attempt,
                parent=invocation.invocation_id,
            )
            await self._store.start_invocation(
                review_invocation,
                reason_summary="核对真实证据、医生职责与风格",
                started_at=self._clock(),
            )
            check = await self._coach.review(
                invocation=review_invocation,
                evidence=evidence,
                style=selection.snapshot,
                reply=generated.output,
            )
            reviewed = True
            review = await self._stage(
                request,
                review_invocation,
                check,
                "coach_grounding_review",
                review_invocation.input_artifact_ids,
            )
            calls += check.model_call_count
            tokens += check.total_token_count
            if check.output and all(
                (
                    check.output.fidelity_passed,
                    check.output.role_passed,
                    check.output.expression_passed,
                )
            ):
                outgoing.append(
                    OutgoingTextMessage(
                        participant=ParticipantRole.COACH,
                        text=generated.output.text,
                        source_refs=generated.output.source_refs,
                        style_profile_version=selection.snapshot.profile.version,
                    )
                )
                failure = None
                parents = (*review_invocation.input_artifact_ids, review.artifact_id)
                break
            failure = check.failure_code or "coach_grounding_rejected"
            if check.output is None:
                # A broken reviewer cannot justify changing an otherwise valid
                # candidate. Its own structured retry has already been exhausted.
                break
            feedback = (
                tuple(i.model_dump(mode="json") for i in check.output.issues)
                if check.output
                else (
                    {
                        "code": failure,
                        "repair_requirement": "基于原始证据重新作答，保留不确定性和医生职责",
                    },
                )
            )
            if check.output and any(i.severity == "blocking" for i in check.output.issues):
                break
            parents = (facts.artifact_id, resolution.artifact_id, review.artifact_id)
        if not outgoing:
            outgoing.append(reply_failure_notice())
        messages = compose_messages(
            turn_id=request.turn_id,
            text_messages=tuple(outgoing) + notices,
            tool_outcomes=request.tool_outcomes,
            created_at=self._clock(),
        )
        status = InvocationStatus.DEGRADED if failure else InvocationStatus.SUCCEEDED
        final = await self._artifact(
            request,
            ArtifactProducerRole.COACH_RESPONSE,
            "outgoing_messages",
            {
                "messages": [m.model_dump(mode="json") for m in messages],
                "failure_code": failure,
                "status": status.value,
                "style_profile_version": selection.snapshot.profile.version,
            },
            parents,
        )
        await self._recorder.record_workflow_event(
            turn_id=request.turn_id,
            event_type=ItemType.RESPONSE_DEGRADED if failure else ItemType.RESPONSE_ADOPTED,
            payload={
                "artifact_id": final.artifact_id,
                "mode": "core_primary",
                "reason_code": failure,
                "fallback_type": "system_notice" if failure else None,
                "style_profile_version": selection.snapshot.profile.version,
                "final": True,
            },
        )
        return ResponseFinalizationResult(
            text="\n".join(m.text for m in messages if m.text),
            status=status,
            evidence_artifact_id=facts.artifact_id,
            final_output_artifact_id=final.artifact_id,
            style_profile_version=selection.snapshot.profile.version,
            model_call_count=calls,
            total_token_count=tokens,
            reviewer_ran=reviewed,
            reply_repaired=attempt == 2,
            failure_code=failure,
            messages=messages,
        )

    def _invocation(
        self,
        request: ResponseFinalizationRequest,
        role: AgentRole,
        version: str,
        parents: tuple[str, ...],
        attempt: int,
        parent: str | None = None,
    ) -> AgentInvocation:
        return AgentInvocation(
            invocation_id=f"inv-{uuid4()}",
            trace_id=request.trace_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            graph_version=self._graph,
            agent_role=role,
            agent_version=version,
            parent_invocation_id=parent or request.core_invocation_id,
            caller="response_finalizer",
            input_artifact_ids=parents,
            input_schema="CoachEvidence",
            attempt=attempt,
            privacy_scopes=(
                "current_user_input",
                "working_memory",
                "profile",
                "tool_results",
                "style_profile",
            ),
            deadline_at=request.deadline_at,
        )

    async def _stage(
        self,
        request: ResponseFinalizationRequest,
        invocation: AgentInvocation,
        result: InvocationRunResult[Any],
        kind: str,
        parents: tuple[str, ...],
    ) -> AgentArtifact:
        payload = {
            "output": result.output.model_dump(mode="json") if result.output else None,
            "failure_code": result.failure_code,
            "validation_errors": result.validation_errors,
            "attempt": invocation.attempt,
            "model_outputs": [r.message.content for r in result.responses],
        }
        artifact = await self._artifact(
            request,
            ArtifactProducerRole(invocation.agent_role.value),
            kind,
            payload,
            parents,
            invocation.invocation_id,
        )
        await self._store.complete_invocation(
            AgentResult(
                invocation_id=invocation.invocation_id,
                status=result.status,
                output_schema=type(result.output).__name__ if result.output else kind,
                output_schema_version="1",
                artifact_id=artifact.artifact_id,
                model_call_count=result.model_call_count,
                tool_call_count=0,
                token_usage=result.total_token_count,
                failure_code=result.failure_code,
            ),
            completed_at=self._clock(),
        )
        return artifact

    async def _artifact(
        self,
        request: ResponseFinalizationRequest,
        producer: ArtifactProducerRole,
        kind: str,
        payload: dict[str, Any],
        parents: tuple[str, ...] = (),
        invocation_id: str | None = None,
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
        await self._store.append_artifact(artifact, invocation_id=invocation_id)
        return artifact
