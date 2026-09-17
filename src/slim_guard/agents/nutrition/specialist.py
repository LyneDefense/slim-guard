"""One bounded Nutrition Agent capability invoked by the Core Agent."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.contracts import ProfessionalAssessment
from slim_guard.agents.nutrition.agent import DEFAULT_NUTRITION_PROMPT_VERSION, NutritionAgent
from slim_guard.agents.nutrition.context import NutritionContextCompiler
from slim_guard.agents.nutrition.contracts import (
    CalculationObservation,
    KnowledgeCorpusStatus,
    KnowledgeRetrieval,
)
from slim_guard.agents.nutrition.knowledge import (
    CitationValidationPolicy,
    KnowledgeCandidateBinder,
)
from slim_guard.agents.nutrition.tools import NutritionToolRegistry, NutritionToolResult
from slim_guard.orchestration.evidence import EvidenceBuilder, EvidenceItem, EvidencePacket
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    ContractModel,
    InvocationStatus,
)
from slim_guard.runtime.invocation import InvocationGrant, InvocationRunner, InvocationStore


class NutritionConsultationRequest(ContractModel):
    """Privacy-bounded request created by trusted runtime code, not by a model."""

    trace_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    parent_invocation_id: str = Field(min_length=1, max_length=128)
    user_request: str = Field(min_length=1, max_length=4000)
    professional_question: str = Field(min_length=1, max_length=2000)
    current_items: tuple[dict[str, Any], ...] = Field(default=(), max_length=80)
    authoritative_context: dict[str, Any] = Field(default_factory=dict)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_aware_deadline(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Nutrition consultation deadline must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class NutritionConsultationResult:
    status: InvocationStatus
    assessment: ProfessionalAssessment
    invocation: AgentInvocation
    artifact: AgentArtifact
    evidence_artifact: AgentArtifact
    inputs_artifact: AgentArtifact
    model_call_count: int
    total_token_count: int
    tool_call_count: int
    failure_code: str | None = None


class NutritionSpecialist:
    """Build evidence, retrieve governed knowledge, and run Nutrition Agent once."""

    def __init__(
        self,
        *,
        model: ModelGateway,
        model_name: str,
        graph_version: str,
        nutrition_tools: NutritionToolRegistry,
        invocation_store: InvocationStore | None = None,
        max_total_tokens: int = 32_000,
        evidence_builder: EvidenceBuilder | None = None,
        context_compiler: NutritionContextCompiler | None = None,
        citation_policy: CitationValidationPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_total_tokens < 1:
            raise ValueError("Nutrition invocation token budget must be positive")
        self._clock = clock or (lambda: datetime.now(UTC))
        runner = InvocationRunner(model=model, clock=self._clock)
        self._agent = NutritionAgent(runner=runner, model=model_name)
        self._graph_version = graph_version
        self._nutrition_tools = nutrition_tools
        self._invocation_store = invocation_store
        self._max_total_tokens = max_total_tokens
        self._evidence_builder = evidence_builder or EvidenceBuilder()
        self._context_compiler = context_compiler or NutritionContextCompiler()
        self._binder = KnowledgeCandidateBinder()
        self._citation_policy = citation_policy or CitationValidationPolicy()

    async def consult(
        self,
        request: NutritionConsultationRequest,
    ) -> NutritionConsultationResult:
        invocation_id = f"inv-{uuid4()}"
        packet = await self._evidence_builder.build(
            turn_id=request.turn_id,
            user_request=request.user_request,
            professional_question=request.professional_question,
            current_items=request.current_items,
            authoritative_context=request.authoritative_context,
        )
        observations, knowledge, receipts = await self._nutrition_inputs(
            packet,
            invocation_id=invocation_id,
        )
        evidence_artifact = self._artifact(
            turn_id=request.turn_id,
            producer=ArtifactProducerRole.EVIDENCE_BUILDER,
            artifact_type="evidence_packet",
            payload=packet.model_dump(mode="json"),
        )
        inputs_artifact = self._artifact(
            turn_id=request.turn_id,
            producer=ArtifactProducerRole.NUTRITION_TOOL,
            artifact_type="nutrition_inputs",
            payload={
                "observations": [item.model_dump(mode="json") for item in observations],
                "knowledge": knowledge.model_dump(mode="json"),
                "tool_receipts": receipts,
            },
            parents=(evidence_artifact.artifact_id,),
        )
        invocation = AgentInvocation(
            invocation_id=invocation_id,
            trace_id=request.trace_id,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            graph_version=self._graph_version,
            agent_role=AgentRole.NUTRITION_EXPERT,
            agent_version=DEFAULT_NUTRITION_PROMPT_VERSION,
            caller=AgentRole.CORE.value,
            parent_invocation_id=request.parent_invocation_id,
            input_artifact_ids=(
                evidence_artifact.artifact_id,
                inputs_artifact.artifact_id,
            ),
            input_schema="NutritionContext",
            allowed_tools=self._nutrition_tools.names,
            privacy_scopes=("evidence_packet", "nutrition_observations"),
            deadline_at=request.deadline_at,
            max_model_calls=2,
            max_tool_calls=8,
            max_total_tokens=self._max_total_tokens,
            payload={
                "evidence_count": len(packet.items),
                "calculation_count": len(observations),
                "corpus_status": knowledge.corpus_status.value,
            },
        )
        await self._persist_inputs(evidence_artifact, inputs_artifact)
        if self._invocation_store is not None:
            await self._invocation_store.start_invocation(
                invocation,
                reason_summary="基于当前任务证据和已发布营养资料形成专业评估",
                started_at=self._aware_now(),
            )
        context = self._context_compiler.compile(
            packet,
            calculation_observations=observations,
            knowledge=knowledge,
        )
        # The specialist boundary owns the read-only tools above. The structured
        # Nutrition Agent receives only the resulting evidence context and has no
        # executable tools of its own, which prevents a model from bypassing the
        # deterministic retrieval/calculation path.
        model_invocation = invocation.model_copy(
            update={"allowed_tools": (), "max_tool_calls": 0}
        )
        result = await self._agent.run(
            invocation=model_invocation,
            context=context,
            grant=InvocationGrant(
                agent_role=AgentRole.NUTRITION_EXPERT,
                allowed_tools=frozenset(),
                privacy_scopes=frozenset(invocation.privacy_scopes),
                max_model_calls=invocation.max_model_calls,
                max_tool_calls=0,
                max_total_tokens=invocation.max_total_tokens,
            ),
        )
        artifact = self._artifact(
            turn_id=request.turn_id,
            producer=ArtifactProducerRole.NUTRITION_EXPERT,
            artifact_type=(
                "conservative_assessment" if result.used_fallback else "professional_assessment"
            ),
            payload=result.assessment.model_dump(mode="json"),
            parents=(evidence_artifact.artifact_id, inputs_artifact.artifact_id),
        )
        if self._invocation_store is not None:
            await self._invocation_store.append_artifact(
                artifact,
                invocation_id=invocation.invocation_id,
            )
            await self._invocation_store.complete_invocation(
                AgentResult(
                    invocation_id=invocation.invocation_id,
                    status=result.status,
                    output_schema=ProfessionalAssessment.__name__,
                    output_schema_version="1",
                    artifact_id=artifact.artifact_id,
                    model_call_count=result.model_call_count,
                    tool_call_count=len(receipts),
                    token_usage=result.total_token_count,
                    failure_code=result.failure_code,
                ),
                completed_at=self._aware_now(),
            )
        return NutritionConsultationResult(
            status=result.status,
            assessment=result.assessment,
            invocation=invocation,
            artifact=artifact,
            evidence_artifact=evidence_artifact,
            inputs_artifact=inputs_artifact,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
            tool_call_count=len(receipts),
            failure_code=result.failure_code,
        )

    async def _persist_inputs(
        self,
        evidence: AgentArtifact,
        inputs: AgentArtifact,
    ) -> None:
        if self._invocation_store is None:
            return
        await self._invocation_store.append_artifact(evidence)
        await self._invocation_store.append_artifact(inputs)

    async def _nutrition_inputs(
        self,
        packet: EvidencePacket,
        *,
        invocation_id: str,
    ) -> tuple[
        tuple[CalculationObservation, ...],
        KnowledgeRetrieval,
        list[dict[str, object]],
    ]:
        observations: list[CalculationObservation] = []
        receipts: list[dict[str, object]] = []
        weight_items = [item for item in packet.items if item.source_type.value == "weight_record"]
        timed_weights = [item for item in weight_items if item.occurred_at is not None]
        if timed_weights:
            trend = await self._nutrition_tools.execute(
                "calculate_weight_trend",
                {
                    "measurements": [
                        {
                            "weight_kg": item.content.get("weight_kg"),
                            "measured_at": item.occurred_at,
                            "evidence_id": item.evidence_id,
                        }
                        for item in timed_weights
                    ]
                },
            )
            receipts.append(self._tool_receipt("calculate_weight_trend", trend))
            if trend.status.value == "succeeded" and trend.output.get("status") == "calculated":
                observations.append(
                    CalculationObservation(
                        observation_id=f"calc-trend-{uuid4()}",
                        calculation_type="calculate_weight_trend",
                        value=float(trend.output["change_kg"]),
                        unit="kg",
                        inputs=dict(trend.output),
                    )
                )

        height_item, height_cm = self._height_evidence(packet)
        if weight_items and height_item is not None and height_cm is not None:
            latest_weight = max(
                weight_items,
                key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC),
            )
            bmi = await self._nutrition_tools.execute(
                "calculate_bmi",
                {
                    "weight_kg": latest_weight.content.get("weight_kg"),
                    "height_cm": height_cm,
                    "evidence_refs": (latest_weight.evidence_id, height_item.evidence_id),
                },
            )
            receipts.append(self._tool_receipt("calculate_bmi", bmi))
            if bmi.status.value == "succeeded":
                observations.append(
                    CalculationObservation(
                        observation_id=f"calc-bmi-{uuid4()}",
                        calculation_type="calculate_bmi",
                        value=float(bmi.output["value"]),
                        unit="kg/m2",
                        inputs={
                            **dict(bmi.output.get("inputs", {})),
                            "evidence_refs": list(bmi.source_ids),
                        },
                    )
                )

        knowledge_result = await self._nutrition_tools.execute(
            "search_nutrition_knowledge",
            {"query": packet.professional_question, "max_results": 5},
        )
        receipts.append(self._tool_receipt("search_nutrition_knowledge", knowledge_result))
        if knowledge_result.status.value != "succeeded":
            knowledge = KnowledgeRetrieval(corpus_status=KnowledgeCorpusStatus.ERROR)
        else:
            try:
                knowledge = self._binder.bind_search_result(
                    invocation_id=invocation_id,
                    result=knowledge_result.output,
                    policy=self._citation_policy,
                )
            except (KeyError, TypeError, ValueError):
                knowledge = KnowledgeRetrieval(corpus_status=KnowledgeCorpusStatus.ERROR)
        return tuple(observations), knowledge, receipts

    @staticmethod
    def _height_evidence(packet: EvidencePacket) -> tuple[EvidenceItem | None, float | None]:
        for item in packet.items:
            if item.source_type.value != "profile_memory":
                continue
            key = str(item.content.get("key", "")).casefold()
            if "height" not in key and "身高" not in key:
                continue
            raw = item.content.get("value")
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                continue
            height = float(raw)
            if 0 < height <= 3:
                height *= 100
            if 50 <= height <= 300:
                return item, height
        return None, None

    @staticmethod
    def _tool_receipt(name: str, result: NutritionToolResult) -> dict[str, object]:
        return {
            "tool_name": name,
            "tool_version": "1",
            "effect_level": "read",
            "status": result.status.value,
            "output": dict(result.output),
            "source_ids": list(result.source_ids),
            "failure": (
                result.failure.model_dump(mode="json") if result.failure is not None else None
            ),
        }

    def _artifact(
        self,
        *,
        turn_id: str,
        producer: ArtifactProducerRole,
        artifact_type: str,
        payload: Mapping[str, Any],
        parents: Sequence[str] = (),
    ) -> AgentArtifact:
        return AgentArtifact.create(
            artifact_id=f"artifact-{uuid4()}",
            turn_id=turn_id,
            producer_role=producer,
            artifact_type=artifact_type,
            schema_version="1",
            parent_artifact_ids=tuple(parents),
            payload=dict(payload),
            created_at=self._aware_now(),
        )

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Nutrition specialist clock must be timezone-aware")
        return now


__all__ = [
    "NutritionConsultationRequest",
    "NutritionConsultationResult",
    "NutritionSpecialist",
]
