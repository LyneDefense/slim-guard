"""Response Style Agent with bounded repair and deterministic fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.contracts import AgentInvocation, AgentRole, InvocationStatus, StyledResponse
from slim_guard.agents.structured_runner import StructuredAgentRunner, StructuredRunResult
from slim_guard.agents.style.contracts import StyleContext
from slim_guard.agents.style.renderer import NeutralRenderer
from slim_guard.agents.style.validation import StyleResponseValidator, StyleValidationReport
from slim_guard.orchestration.graph import InvocationGrant

RESPONSE_STYLE_PROMPT_VERSION = "response-style-v1"
RESPONSE_STYLE_PROMPT = (
    "You are SlimGuard's response-style renderer. Change expression only. "
    "Do not add, remove, weaken, strengthen, or reinterpret any fact, claim, "
    "action, risk, uncertainty, source reference, citation, number, unit, time, "
    "or record status. Protected content should remain verbatim apart from "
    "punctuation. Never reveal hidden reasoning. Return only one JSON object "
    "matching StyledResponse."
)


@dataclass(frozen=True, slots=True)
class StyleAgentResult:
    """A style result always contains a renderable response, including failures."""

    status: InvocationStatus
    response: StyledResponse
    model_responses: tuple[ModelResponse, ...]
    model_call_count: int
    total_token_count: int
    used_fallback: bool
    repair_attempted: bool
    failure_code: str | None = None
    validation_report: StyleValidationReport = StyleValidationReport()

    @property
    def styled_response(self) -> StyledResponse:
        return self.response


class ResponseStyleAgent:
    """Apply a versioned profile without changing upstream meaning or provenance."""

    def __init__(
        self,
        *,
        runner: StructuredAgentRunner,
        model: str,
        validator: StyleResponseValidator | None = None,
        neutral_renderer: NeutralRenderer | None = None,
        prompt_version: str = RESPONSE_STYLE_PROMPT_VERSION,
    ) -> None:
        if not model.strip():
            raise ValueError("Style model cannot be blank")
        if not prompt_version.strip():
            raise ValueError("Style prompt version cannot be blank")
        self._runner = runner
        self._model = model
        self._validator = validator or StyleResponseValidator()
        self._neutral_renderer = neutral_renderer or NeutralRenderer()
        self._prompt_version = prompt_version

    async def run(
        self,
        *,
        invocation: AgentInvocation,
        context: StyleContext,
        grant: InvocationGrant | None = None,
        review_feedback: tuple[str, ...] = (),
    ) -> StyleAgentResult:
        """Run at most two calls: the initial JSON response and one repair."""

        boundary_failure = self._boundary_failure(invocation, context)
        if boundary_failure is not None:
            return self._fallback(context=context, failure_code=boundary_failure)

        request = self._request(
            invocation=invocation,
            context=context,
            review_feedback=review_feedback,
        )
        responses: list[ModelResponse] = []
        total_tokens = 0
        last_report = StyleValidationReport()
        last_failure = "style_generation_failed"
        repair_attempted = False

        try:
            first = await self._single_call(
                invocation=invocation,
                request=request,
                grant=grant,
                remaining_tokens=invocation.max_total_tokens,
            )
            responses.extend(first.responses)
            total_tokens += first.total_token_count
            if first.output is not None:
                last_report = self._validator.validate(context, first.output)
                if last_report.is_valid:
                    return self._success(
                        response=first.output,
                        responses=responses,
                        total_tokens=total_tokens,
                        repair_attempted=False,
                    )
                last_failure = "style_integrity_invalid"
            else:
                last_failure = first.failure_code or "style_generation_failed"

            if first.output is None and not self._is_repairable_failure(last_failure):
                return self._fallback(
                    context=context,
                    failure_code=last_failure,
                    responses=responses,
                    total_tokens=total_tokens,
                    validation_report=last_report,
                )

            remaining_calls = invocation.max_model_calls - len(responses)
            remaining_tokens = invocation.max_total_tokens - total_tokens
            if remaining_calls <= 0 or remaining_tokens <= 0:
                return self._fallback(
                    context=context,
                    failure_code=last_failure,
                    responses=responses,
                    total_tokens=total_tokens,
                    validation_report=last_report,
                )

            repair_attempted = True
            repair_request = self._repair_request(
                request=request,
                previous=responses[-1] if responses else None,
                validation_report=last_report,
            )
            repaired = await self._single_call(
                invocation=invocation,
                request=repair_request,
                grant=grant,
                remaining_tokens=remaining_tokens,
            )
            responses.extend(repaired.responses)
            total_tokens += repaired.total_token_count
            if repaired.output is not None:
                last_report = self._validator.validate(context, repaired.output)
                if last_report.is_valid:
                    return self._success(
                        response=repaired.output,
                        responses=responses,
                        total_tokens=total_tokens,
                        repair_attempted=True,
                    )
                last_failure = "style_integrity_invalid_after_repair"
            else:
                last_failure = repaired.failure_code or "style_repair_failed"
        except Exception:
            # Style is an optional expression layer. Provider, validation and wiring
            # failures must not prevent the upstream plan from being rendered.
            last_failure = "style_internal_error"

        return self._fallback(
            context=context,
            failure_code=last_failure,
            responses=responses,
            total_tokens=total_tokens,
            repair_attempted=repair_attempted,
            validation_report=last_report,
        )

    async def _single_call(
        self,
        *,
        invocation: AgentInvocation,
        request: ModelRequest,
        grant: InvocationGrant | None,
        remaining_tokens: int,
    ) -> StructuredRunResult[StyledResponse]:
        single_call_invocation = invocation.model_copy(
            update={"max_model_calls": 1, "max_total_tokens": remaining_tokens}
        )
        return await self._runner.run(
            invocation=single_call_invocation,
            request=request.model_copy(
                update={"max_output_tokens": min(request.max_output_tokens, remaining_tokens)}
            ),
            output_type=StyledResponse,
            grant=grant,
        )

    def _request(
        self,
        *,
        invocation: AgentInvocation,
        context: StyleContext,
        review_feedback: tuple[str, ...] = (),
    ) -> ModelRequest:
        system = ModelMessage(
            role=MessageRole.SYSTEM,
            content=RESPONSE_STYLE_PROMPT,
        )
        user = ModelMessage(
            role=MessageRole.USER,
            content=(
                context.model_dump_json()
                if not review_feedback
                else json.dumps(
                    {
                        "style_context": context.model_dump(mode="json"),
                        "review_feedback_issue_types": list(review_feedback),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
        )
        return ModelRequest(
            purpose=ModelPurpose.RESPONSE_STYLE,
            model=self._model,
            messages=(system, user),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=StyledResponse.__name__,
            max_output_tokens=min(2048, invocation.max_total_tokens),
            temperature=0,
            metadata={
                "invocation_id": invocation.invocation_id,
                "style_profile_version": context.profile.version,
                "prompt_version": self._prompt_version,
            },
        )

    @staticmethod
    def _repair_request(
        *,
        request: ModelRequest,
        previous: ModelResponse | None,
        validation_report: StyleValidationReport,
    ) -> ModelRequest:
        messages = list(request.messages)
        if previous is not None:
            messages.append(previous.message)
        issue_codes = validation_report.issue_codes or ("structured_output_invalid",)
        messages.append(
            ModelMessage(
                role=MessageRole.USER,
                content=(
                    "Repair the prior JSON once. Return only a complete StyledResponse JSON "
                    "object. Preserve all protected content and exact reference sets. "
                    "Validation issue codes: "
                    + json.dumps(issue_codes, ensure_ascii=False)
                ),
            )
        )
        return request.model_copy(update={"messages": tuple(messages)})

    @staticmethod
    def _boundary_failure(invocation: AgentInvocation, context: StyleContext) -> str | None:
        if invocation.agent_role is not AgentRole.RESPONSE_STYLE:
            return "style_invocation_role_mismatch"
        if invocation.turn_id != context.turn_id:
            return "style_context_turn_mismatch"
        if invocation.allowed_tools or invocation.max_tool_calls != 0:
            return "style_tools_not_allowed"
        return None

    @staticmethod
    def _is_repairable_failure(failure_code: str) -> bool:
        return failure_code in {
            "structured_output_invalid",
            "structured_output_missing",
        }

    @staticmethod
    def _success(
        *,
        response: StyledResponse,
        responses: list[ModelResponse],
        total_tokens: int,
        repair_attempted: bool,
    ) -> StyleAgentResult:
        return StyleAgentResult(
            status=InvocationStatus.SUCCEEDED,
            response=response,
            model_responses=tuple(responses),
            model_call_count=len(responses),
            total_token_count=total_tokens,
            used_fallback=False,
            repair_attempted=repair_attempted,
        )

    def _fallback(
        self,
        *,
        context: StyleContext,
        failure_code: str,
        responses: list[ModelResponse] | None = None,
        total_tokens: int = 0,
        repair_attempted: bool = False,
        validation_report: StyleValidationReport | None = None,
    ) -> StyleAgentResult:
        model_responses = tuple(responses or ())
        return StyleAgentResult(
            status=InvocationStatus.DEGRADED,
            response=self._neutral_renderer.render(context),
            model_responses=model_responses,
            model_call_count=len(model_responses),
            total_token_count=total_tokens,
            used_fallback=True,
            repair_attempted=repair_attempted,
            failure_code=failure_code,
            validation_report=validation_report or StyleValidationReport(),
        )


__all__ = [
    "RESPONSE_STYLE_PROMPT",
    "RESPONSE_STYLE_PROMPT_VERSION",
    "ResponseStyleAgent",
    "StyleAgentResult",
]
