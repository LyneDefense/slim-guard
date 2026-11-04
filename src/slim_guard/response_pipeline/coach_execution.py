"""Record coach calls and artifacts; dialogue policy lives in the coach capability."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from slim_guard.agents.coach import (
    COACH_PROMPT_VERSION,
    COACH_REVIEW_VERSION,
    CoachEvidence,
    CoachReply,
    CoachResponder,
)
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.harness.events import ItemType
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
)
from slim_guard.runtime.invocation import InvocationRunResult, InvocationStore

from .contracts import ResponseFinalizationRequest


class RecordedCoachTurn:
    def __init__(
        self,
        *,
        coach: CoachResponder,
        request: ResponseFinalizationRequest,
        evidence: CoachEvidence,
        style: StyleProfileSnapshot,
        store: InvocationStore,
        recorder: HarnessRunRecorder,
        graph: str,
        clock: Callable[[], datetime],
        parents: tuple[str, ...],
    ) -> None:
        self.coach, self.request, self.evidence, self.style = coach, request, evidence, style
        self.store, self.recorder, self.graph, self.clock = store, recorder, graph, clock
        self.parents = parents
        self.review_artifacts: dict[int, tuple[str, ...]] = {}
        self._facts = parents
        self._generation: AgentInvocation | None = None
        self._candidate: AgentArtifact | None = None

    async def generate(
        self, *, attempt: int, feedback: tuple[dict[str, object], ...], previous_reply: str | None
    ) -> InvocationRunResult[CoachReply]:
        invocation = self._invocation(AgentRole.COACH_RESPONSE, COACH_PROMPT_VERSION, attempt)
        self._generation = invocation
        await self.store.start_invocation(
            invocation, reason_summary="直接按医生风格回应用户", started_at=self.clock()
        )
        result = await self.coach.generate(
            invocation=invocation,
            evidence=self.evidence,
            style=self.style,
            feedback=feedback,
            previous_reply=previous_reply,
        )
        self._candidate = await self._stage(invocation, result, "coach_reply")
        return result

    async def review(self, *, attempt: int, reply: CoachReply) -> InvocationRunResult[ReplyCheck]:
        assert self._generation is not None and self._candidate is not None
        self.parents = (*self._facts, self._candidate.artifact_id)
        invocation = self._invocation(
            AgentRole.COACH_REVIEW,
            COACH_REVIEW_VERSION,
            attempt,
            parent=self._generation.invocation_id,
        )
        await self.store.start_invocation(
            invocation, reason_summary="核对真实证据、医生职责与风格", started_at=self.clock()
        )
        result = await self.coach.review(
            invocation=invocation,
            evidence=self.evidence,
            style=self.style,
            reply=reply,
        )
        artifact = await self._stage(invocation, result, "coach_grounding_review")
        self.parents = (*invocation.input_artifact_ids, artifact.artifact_id)
        self.review_artifacts[attempt] = self.parents
        return result

    def _invocation(
        self,
        role: AgentRole,
        version: str,
        attempt: int,
        parent: str | None = None,
    ) -> AgentInvocation:
        return AgentInvocation(
            invocation_id=f"inv-{uuid4()}",
            trace_id=self.request.trace_id,
            thread_id=self.request.thread_id,
            turn_id=self.request.turn_id,
            graph_version=self.graph,
            agent_role=role,
            agent_version=version,
            parent_invocation_id=parent or self.request.core_invocation_id,
            caller="response_finalizer",
            input_artifact_ids=self.parents,
            input_schema="CoachEvidence",
            attempt=attempt,
            privacy_scopes=(
                "current_user_input",
                "working_memory",
                "profile",
                "tool_results",
                "style_profile",
            ),
            deadline_at=self.request.deadline_at,
        )

    async def _stage(
        self,
        invocation: AgentInvocation,
        result: InvocationRunResult[Any],
        kind: str,
    ) -> AgentArtifact:
        for rejected in result.validation_errors:
            await self.recorder.record_workflow_event(
                turn_id=self.request.turn_id,
                event_type=ItemType.STRUCTURED_OUTPUT_REJECTED,
                payload={
                    "schema": rejected.get("schema") or kind,
                    "errors_json": json.dumps(rejected.get("errors", []), ensure_ascii=False),
                    "attempt": rejected.get("attempt", 1),
                },
            )
        artifact = AgentArtifact.create(
            artifact_id=f"artifact-{uuid4()}",
            turn_id=self.request.turn_id,
            producer_role=ArtifactProducerRole(invocation.agent_role.value),
            artifact_type=kind,
            schema_version="1",
            payload={
                "output": result.output.model_dump(mode="json") if result.output else None,
                "failure_code": result.failure_code,
                "validation_errors": result.validation_errors,
                "attempt": invocation.attempt,
                "model_outputs": [r.message.content for r in result.responses],
            },
            parent_artifact_ids=invocation.input_artifact_ids,
            created_at=self.clock(),
        )
        await self.store.append_artifact(artifact, invocation_id=invocation.invocation_id)
        await self.store.complete_invocation(
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
            completed_at=self.clock(),
        )
        return artifact
