"""Bounded Core Agent repair after a structured reviewer verdict."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.contracts import ProfessionalAssessment, ResponsePlan
from slim_guard.runtime.contracts import (
    AgentInvocation,
    AgentRole,
    ContractModel,
    InvocationStatus,
)
from slim_guard.runtime.invocation import (
    InvocationAuthorizationError,
    InvocationGrant,
    InvocationRunner,
)

CORE_REPAIR_PROMPT_VERSION = "core-response-repair-v1"
CORE_REPAIR_PROMPT = (
    "You are SlimGuard's Core Agent repairing a user-facing neutral content draft after "
    "a Safety & Fidelity review. Use only the supplied ResponsePlan, corrected professional "
    "assessment, and review instruction. Do not invent user facts, tool success, nutrition "
    "claims, citations, diagnoses, or hidden reasoning. When user evidence is missing, ask "
    "the smallest useful clarification instead of guessing. Return only CoreRepairDraft JSON."
)


class CoreRepairContext(ContractModel):
    schema_version: Literal["1"] = "1"
    turn_id: str = Field(min_length=1, max_length=128)
    original_neutral_draft: str = Field(min_length=1, max_length=12_000)
    response_plan: ResponsePlan
    assessment: ProfessionalAssessment | None = None
    issue_types: tuple[str, ...] = Field(min_length=1, max_length=16)
    repair_instruction: str = Field(min_length=1, max_length=2_000)

    @field_validator("issue_types")
    @classmethod
    def validate_issue_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Core repair issue types cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Core repair issue types must be unique")
        return normalized


class CoreRepairDraft(ContractModel):
    schema_version: Literal["1"] = "1"
    neutral_draft: str = Field(min_length=1, max_length=12_000)

    @field_validator("neutral_draft")
    @classmethod
    def normalize_draft(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Core repair draft cannot be blank")
        return normalized


@dataclass(frozen=True, slots=True)
class CoreRepairAgentResult:
    status: InvocationStatus
    draft: CoreRepairDraft | None
    model_responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None = None


class CoreResponseRepairAgent:
    """Repair content ownership without giving the reviewer write authority."""

    def __init__(
        self,
        *,
        runner: InvocationRunner,
        model: str,
        prompt_version: str = CORE_REPAIR_PROMPT_VERSION,
    ) -> None:
        if not model.strip():
            raise ValueError("Core repair model cannot be blank")
        if not prompt_version.strip():
            raise ValueError("Core repair prompt version cannot be blank")
        self._runner = runner
        self._model = model
        self._prompt_version = prompt_version

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        context: CoreRepairContext,
        grant: InvocationGrant | None = None,
    ) -> CoreRepairAgentResult:
        failure = self._boundary_failure(invocation, context)
        if failure is not None:
            return self._failure(failure)
        try:
            result = await self._runner.run(
                invocation=invocation,
                request=self._request(invocation, context),
                output_type=CoreRepairDraft,
                grant=grant,
            )
        except InvocationAuthorizationError:
            return self._failure("core_repair_invocation_unauthorized")
        except Exception:
            return self._failure("core_repair_internal_error")
        if result.output is None:
            return self._failure(
                result.failure_code or "core_repair_generation_failed",
                responses=result.responses,
                tokens=result.total_token_count,
            )
        return CoreRepairAgentResult(
            status=InvocationStatus.SUCCEEDED,
            draft=result.output,
            model_responses=result.responses,
            model_call_count=result.model_call_count,
            total_token_count=result.total_token_count,
        )

    def _request(
        self,
        invocation: AgentInvocation,
        context: CoreRepairContext,
    ) -> ModelRequest:
        return ModelRequest(
            purpose=ModelPurpose.HARNESS_TURN,
            model=self._model,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content=CORE_REPAIR_PROMPT),
                ModelMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        context.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=CoreRepairDraft.__name__,
            max_output_tokens=min(2_048, invocation.max_total_tokens),
            temperature=0,
            metadata={
                "invocation_id": invocation.invocation_id,
                "prompt_version": self._prompt_version,
                "repair_owner": AgentRole.CORE.value,
            },
        )

    @staticmethod
    def _boundary_failure(
        invocation: AgentInvocation,
        context: CoreRepairContext,
    ) -> str | None:
        if invocation.agent_role is not AgentRole.CORE:
            return "core_repair_invocation_role_mismatch"
        if invocation.turn_id != context.turn_id:
            return "core_repair_context_turn_mismatch"
        if invocation.allowed_tools or invocation.max_tool_calls != 0:
            return "core_repair_tools_not_allowed"
        return None

    @staticmethod
    def _failure(
        code: str,
        *,
        responses: tuple[ModelResponse, ...] = (),
        tokens: int = 0,
    ) -> CoreRepairAgentResult:
        return CoreRepairAgentResult(
            status=InvocationStatus.FAILED,
            draft=None,
            model_responses=responses,
            model_call_count=len(responses),
            total_token_count=tokens,
            failure_code=code,
        )


__all__ = [
    "CORE_REPAIR_PROMPT",
    "CORE_REPAIR_PROMPT_VERSION",
    "CoreRepairAgentResult",
    "CoreRepairContext",
    "CoreRepairDraft",
    "CoreResponseRepairAgent",
]
