"""Core-facing Agent Tool for professional nutrition consultation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import Field, field_validator

from slim_guard.agents.nutrition.constants import (
    CONSULT_NUTRITION_TOOL_NAME,
    CONSULT_NUTRITION_TOOL_VERSION,
)
from slim_guard.agents.nutrition.specialist import (
    NutritionConsultationRequest,
    NutritionConsultationResult,
)
from slim_guard.harness.context_data import ContextDataProvider
from slim_guard.harness.events import ItemType
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.observability.tracing import current_trace_id
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

logger = logging.getLogger(__name__)


class ConsultNutritionArguments(ToolArguments):
    professional_question: str = Field(min_length=1, max_length=2000)

    @field_validator("professional_question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Nutrition question cannot be blank")
        return normalized


class NutritionConsultationPort(Protocol):
    async def consult(
        self,
        request: NutritionConsultationRequest,
    ) -> NutritionConsultationResult: ...


class NutritionAgentToolHandler:
    def __init__(
        self,
        *,
        specialist: NutritionConsultationPort,
        state: HarnessStateRepository,
        context_data: ContextDataProvider,
        timeout: timedelta = timedelta(seconds=45),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout <= timedelta(0):
            raise ValueError("Nutrition Agent Tool timeout must be positive")
        self._specialist = specialist
        self._state = state
        self._context_data = context_data
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    async def consult(
        self,
        context: ToolContext,
        arguments: ConsultNutritionArguments,
    ) -> ToolResult:
        if context.agent_invocation_id is None:
            return ToolResult.failed(
                code="missing_parent_invocation",
                message="Nutrition consultation requires a parent Core Agent invocation.",
            )
        turn = await self._state.get_turn(context.turn_id)
        if turn is None or turn.thread_id != context.thread_id:
            return ToolResult.failed(
                code="nutrition_turn_not_found",
                message="The current nutrition consultation Turn could not be verified.",
            )
        now = self._aware_now()
        items = await self._state.list_items(context.turn_id)
        authoritative_context = dict(
            await self._context_data.load(
                user_id=context.user_id,
                current_time=now,
                trigger=turn.trigger,
                input_items=tuple(items),
            )
        )
        user_request = (
            "\n".join(
                str(item.payload.get("text", "")).strip()
                for item in items
                if item.item_type is ItemType.USER_MESSAGE
                and str(item.payload.get("text", "")).strip()
            )
            or arguments.professional_question
        )
        deadline = min(
            turn.deadline_at or now + self._timeout,
            now + self._timeout,
        )
        try:
            result = await self._specialist.consult(
                NutritionConsultationRequest(
                    trace_id=current_trace_id() or context.turn_id,
                    user_id=context.user_id,
                    thread_id=context.thread_id,
                    turn_id=context.turn_id,
                    parent_invocation_id=context.agent_invocation_id,
                    user_request=user_request,
                    professional_question=arguments.professional_question,
                    current_items=tuple(
                        {
                            "id": item.id,
                            "item_type": item.item_type.value,
                            "payload": item.payload,
                        }
                        for item in items
                    ),
                    authoritative_context=authoritative_context,
                    deadline_at=deadline,
                )
            )
        except Exception as error:
            logger.error(
                "nutrition_specialist_tool_failed",
                extra={
                    "error_type": type(error).__name__,
                    "turn_id": context.turn_id,
                },
            )
            return ToolResult.failed(
                code="nutrition_specialist_failed",
                message="The nutrition specialist could not complete this consultation.",
                retryable=True,
            )
        citations = [citation.model_dump(mode="json") for citation in result.assessment.citations]
        return ToolResult.success(
            output={
                "specialist_status": result.status.value,
                "assessment": result.assessment.model_dump(mode="json"),
                "artifact_id": result.artifact.artifact_id,
                "invocation_id": result.invocation.invocation_id,
                "citations": citations,
                "failure_code": result.failure_code,
            },
            source_ids=(
                result.artifact.artifact_id,
                *(citation.citation_id for citation in result.assessment.citations),
            ),
        )

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Nutrition Agent Tool clock must be timezone-aware")
        return now


def nutrition_agent_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=CONSULT_NUTRITION_TOOL_NAME,
            description=(
                "Ask the bounded Nutrition Agent for nutrition analysis grounded in the "
                "current evidence. Approved RAG citations are preferred; when no relevant "
                "citation exists it may return a clearly low-risk, uncertainty-aware "
                "common-knowledge observation, but never a medical or precise nutrient "
                "claim. "
                "Use this only when the user asks for dietary suitability, meal adjustment, "
                "nutrition explanation, an automatic post-record meal evaluation, or another "
                "professional nutrition judgment. Do not "
                "use it for greetings or unrelated conversation. The "
                "specialist reads the current Turn evidence and approved nutrition corpus; "
                "pass a concise professional question without inventing user facts."
            ),
            version=CONSULT_NUTRITION_TOOL_VERSION,
            arguments_model=ConsultNutritionArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=60,
        ),
    )


def nutrition_agent_tool_executors(
    handler: NutritionAgentToolHandler,
) -> dict[str, ToolExecutor[ConsultNutritionArguments]]:
    return {
        CONSULT_NUTRITION_TOOL_NAME: ToolExecutor(
            arguments_model=ConsultNutritionArguments,
            handler=handler.consult,
        )
    }


__all__ = [
    "CONSULT_NUTRITION_TOOL_NAME",
    "CONSULT_NUTRITION_TOOL_VERSION",
    "ConsultNutritionArguments",
    "NutritionAgentToolHandler",
    "NutritionConsultationPort",
    "nutrition_agent_tool_definitions",
    "nutrition_agent_tool_executors",
]
