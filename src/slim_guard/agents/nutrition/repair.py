"""Reviewer-directed Nutrition Agent repair over frozen invocation inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator

from slim_guard.agents.contracts import ProfessionalAssessment
from slim_guard.agents.nutrition.agent import DEFAULT_NUTRITION_PROMPT_VERSION, NutritionAgent
from slim_guard.agents.nutrition.context import NutritionContextCompiler
from slim_guard.agents.nutrition.contracts import CalculationObservation, KnowledgeRetrieval
from slim_guard.agents.nutrition.knowledge import (
    CitationValidationPolicy,
    KnowledgeCandidateBinder,
)
from slim_guard.orchestration.evidence import EvidencePacket
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    ContractModel,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationGrant, InvocationStore


class NutritionRepairRequest(ContractModel):
    """Repair request tied to the original assessment and reviewer verdict."""

    trace_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    parent_invocation_id: str = Field(min_length=1, max_length=128)
    assessment_artifact_id: str = Field(min_length=1, max_length=128)
    verdict_artifact_id: str = Field(min_length=1, max_length=128)
    review_feedback: tuple[str, ...] = Field(min_length=1, max_length=16)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_aware_deadline(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Nutrition repair deadline must be timezone-aware")
        return value

    @field_validator("review_feedback")
    @classmethod
    def validate_feedback(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Nutrition repair feedback cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Nutrition repair feedback must be unique")
        return normalized


@dataclass(frozen=True, slots=True)
class NutritionRepairResult:
    status: InvocationStatus
    assessment: ProfessionalAssessment
    invocation: AgentInvocation
    artifact: AgentArtifact
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None


class NutritionRepairService:
    """Re-run Nutrition Agent without changing the original evidence or RAG release."""

    def __init__(
        self,
        *,
        agent: NutritionAgent,
        store: InvocationStore,
        graph_version: str,
        max_total_tokens: int,
        context_compiler: NutritionContextCompiler,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._agent = agent
        self._store = store
        self._graph_version = graph_version
        self._max_total_tokens = max_total_tokens
        self._context_compiler = context_compiler
        self._binder = KnowledgeCandidateBinder()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def repair(self, request: NutritionRepairRequest) -> NutritionRepairResult:
        prior = await self._store.get_artifact(request.assessment_artifact_id)
        verdict = await self._store.get_artifact(request.verdict_artifact_id)
        if (
            prior is None
            or prior.turn_id != request.turn_id
            or prior.producer_role is not ArtifactProducerRole.NUTRITION_EXPERT
        ):
            raise ValueError("Nutrition repair assessment artifact is invalid")
        if (
            verdict is None
            or verdict.turn_id != request.turn_id
            or verdict.producer_role is not ArtifactProducerRole.RESPONSE_REVIEWER
        ):
            raise ValueError("Nutrition repair verdict artifact is invalid")
        evidence_artifact, inputs_artifact = await self._input_artifacts(prior)
        packet = EvidencePacket.model_validate(evidence_artifact.payload)
        invocation_id = f"inv-{uuid4()}"
        observations = tuple(
            CalculationObservation.model_validate(item)
            for item in inputs_artifact.payload.get("observations", ())
        )
        frozen_knowledge = KnowledgeRetrieval.model_validate(
            inputs_artifact.payload.get("knowledge", {"corpus_status": "error"})
        )
        knowledge_snapshot = inputs_artifact.payload.get("knowledge_snapshot")
        knowledge = self._binder.bind_candidates(
            invocation_id=invocation_id,
            candidates=frozen_knowledge.candidates,
            policy=CitationValidationPolicy(
                required_applicability=frozen_knowledge.required_applicability
            ),
            corpus_status=frozen_knowledge.corpus_status,
            query_summary=frozen_knowledge.query_summary,
        )
        invocation = AgentInvocation(
            invocation_id=invocation_id,
            trace_id=request.trace_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            graph_version=self._graph_version,
            agent_role=AgentRole.NUTRITION_EXPERT,
            agent_version=DEFAULT_NUTRITION_PROMPT_VERSION,
            attempt=2,
            caller=AgentRole.RESPONSE_REVIEWER.value,
            parent_invocation_id=request.parent_invocation_id,
            input_artifact_ids=(
                evidence_artifact.artifact_id,
                inputs_artifact.artifact_id,
                prior.artifact_id,
                verdict.artifact_id,
            ),
            input_schema="NutritionContext",
            privacy_scopes=("evidence_packet", "nutrition_observations"),
            deadline_at=request.deadline_at,
            max_model_calls=2,
            max_tool_calls=0,
            max_total_tokens=self._max_total_tokens,
            payload={
                "repair_feedback": list(request.review_feedback),
                "frozen_candidate_count": len(knowledge.candidates),
                "frozen_citation_count": len(knowledge.citations),
                "knowledge_snapshot": knowledge_snapshot,
            },
        )
        await self._store.start_invocation(
            invocation,
            reason_summary="按审查结果修复专业评估，不切换本轮已冻结的营养证据",
            started_at=self._aware_now(),
        )
        context = self._context_compiler.compile(
            packet,
            calculation_observations=observations,
            knowledge=knowledge,
        )
        result = await self._agent.run(
            invocation=invocation,
            context=context,
            grant=InvocationGrant(
                agent_role=AgentRole.NUTRITION_EXPERT,
                allowed_tools=frozenset(),
                privacy_scopes=frozenset(invocation.privacy_scopes),
                max_model_calls=invocation.max_model_calls,
                max_tool_calls=0,
                max_total_tokens=invocation.max_total_tokens,
            ),
            review_feedback=request.review_feedback,
        )
        artifact = self._artifact(
            turn_id=request.turn_id,
            payload={
                **result.assessment.model_dump(mode="json"),
                "attempt": 2,
                "repair_feedback": list(request.review_feedback),
            },
            parents=invocation.input_artifact_ids,
            conservative=result.used_fallback,
        )
        await self._store.append_artifact(
            artifact,
            invocation_id=invocation.invocation_id,
        )
        await self._store.complete_invocation(
            AgentResult(
                invocation_id=invocation.invocation_id,
                status=result.status,
                output_schema=ProfessionalAssessment.__name__,
                output_schema_version="1",
                artifact_id=artifact.artifact_id,
                model_call_count=result.model_call_count,
                tool_call_count=0,
                token_usage=result.total_token_count,
                failure_code=result.failure_code,
            ),
            completed_at=self._aware_now(),
        )
        return NutritionRepairResult(
            status=result.status,
            assessment=result.assessment,
            invocation=invocation,
            artifact=artifact,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
            failure_code=result.failure_code,
        )

    async def _input_artifacts(
        self,
        assessment: AgentArtifact,
    ) -> tuple[AgentArtifact, AgentArtifact]:
        loaded: list[AgentArtifact] = []
        for artifact_id in assessment.parent_artifact_ids:
            artifact = await self._store.get_artifact(artifact_id)
            if artifact is not None:
                loaded.append(artifact)
        evidence = next(
            (item for item in loaded if item.artifact_type == "evidence_packet"),
            None,
        )
        inputs = next(
            (item for item in loaded if item.artifact_type == "nutrition_inputs"),
            None,
        )
        if evidence is None or inputs is None:
            raise ValueError("Nutrition repair could not recover frozen input artifacts")
        return evidence, inputs

    def _artifact(
        self,
        *,
        turn_id: str,
        payload: Mapping[str, Any],
        parents: Sequence[str],
        conservative: bool,
    ) -> AgentArtifact:
        return AgentArtifact.create(
            artifact_id=f"artifact-{uuid4()}",
            turn_id=turn_id,
            producer_role=ArtifactProducerRole.NUTRITION_EXPERT,
            artifact_type=(
                "conservative_assessment" if conservative else "professional_assessment"
            ),
            schema_version="1",
            parent_artifact_ids=tuple(parents),
            payload=dict(payload),
            created_at=self._aware_now(),
        )

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Nutrition repair clock must be timezone-aware")
        return now


__all__ = [
    "NutritionRepairRequest",
    "NutritionRepairResult",
    "NutritionRepairService",
]
