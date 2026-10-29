"""Own output safety, finalization adoption and fallback outside the Core loop."""

import logging
from dataclasses import dataclass

from slim_guard.group_chat.contracts import ChatMessage
from slim_guard.harness.events import ItemType
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.loop import HarnessLoopResult
from slim_guard.harness.safety import OutputGuard, OutputGuardResult, PermissiveOutputGuard
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
    ResponseFinalizer,
)
from slim_guard.response_pipeline.mutation_review import MutationClaimReviewer
from slim_guard.runtime.contracts import AgentInvocation
from slim_guard.usage.context import usage_operation, usage_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessedResponse:
    loop: HarnessLoopResult
    final_text: str | None
    outgoing_messages: tuple[ChatMessage, ...] = ()
    final_output_artifact_id: str | None = None
    finalization: ResponseFinalizationResult | None = None


class ResponseProcessor:
    """Return user-visible output; never complete a Turn or mark delivery successful."""

    def __init__(
        self,
        *,
        recorder: HarnessRunRecorder,
        finalizer: ResponseFinalizer | None = None,
        output_guard: OutputGuard | None = None,
        claim_reviewer: MutationClaimReviewer | None = None,
    ) -> None:
        self._recorder = recorder
        self._finalizer = finalizer
        if getattr(output_guard, "requires_claim_review", False) and claim_reviewer is None:
            raise ValueError("This output guard requires a receipt-bound semantic claim reviewer")
        self._guard = output_guard or PermissiveOutputGuard()
        self._claim_reviewer = claim_reviewer

    async def _review(self, text: str, outcomes: tuple[ToolCallOutcome, ...]) -> OutputGuardResult:
        result = self._guard.review(text=text, tool_outcomes=outcomes)
        if result.modified or self._claim_reviewer is None:
            return result
        return await self._claim_reviewer.review(text=text, tool_outcomes=outcomes)

    @usage_operation("response_processing", source="invocation")
    async def process(
        self,
        *,
        initialized: InitializedTurn,
        invocation: AgentInvocation,
        result: HarnessLoopResult,
        conversation_context: dict[str, object] | None = None,
    ) -> ProcessedResponse:
        if result.termination is not HarnessTermination.FINAL_RESPONSE:
            return ProcessedResponse(result, final_text=None)
        assert result.final_text is not None
        guarded = await self._review(result.final_text, result.tool_outcomes)
        if guarded.modified:
            await self._recorder.record_output_guard(turn_id=invocation.turn_id, code=guarded.code)
            return ProcessedResponse(result, final_text=guarded.text)
        if self._finalizer is None:
            return ProcessedResponse(result, final_text=guarded.text)

        finalization = None
        try:
            with usage_scope(
                invocation_id=invocation.invocation_id,
                agent_role=invocation.agent_role.value,
                component="response_processing",
            ):
                finalization = await self._finalizer.finalize(
                    ResponseFinalizationRequest(
                        trace_id=invocation.trace_id,
                        user_id=initialized.context.user_id,
                        thread_id=initialized.thread.id,
                        turn_id=initialized.turn.id,
                        core_invocation_id=invocation.invocation_id,
                        neutral_draft=guarded.text,
                        messages=result.messages,
                        tool_outcomes=result.tool_outcomes,
                        model_responses=result.model_responses,
                        deadline_at=invocation.deadline_at,
                        conversation_context=conversation_context or {},
                    )
                )
            # Identical text over the same immutable receipt set already has a
            # verdict. Styling/new text still receives its own semantic review.
            checked = (
                guarded
                if finalization.text == guarded.text
                else await self._review(finalization.text, result.tool_outcomes)
            )
            if checked.modified:
                # Preserve the existing fallback policy; role-preserving repair is
                # a separate behavior change, not part of lifecycle extraction.
                await self._degraded(
                    invocation, "response_pipeline_output_guard_modified", finalization
                )
                return ProcessedResponse(result, final_text=guarded.text, finalization=finalization)
            await self._recorder.record_workflow_event(
                turn_id=invocation.turn_id,
                event_type=ItemType.RESPONSE_ADOPTED,
                payload={
                    "artifact_id": finalization.final_output_artifact_id,
                    "mode": "core_primary",
                    "final": True,
                },
            )
            return ProcessedResponse(
                result,
                final_text=checked.text,
                outgoing_messages=finalization.messages,
                final_output_artifact_id=finalization.final_output_artifact_id,
                finalization=finalization,
            )
        except Exception:
            logger.exception("final_response_workflow_failed")
            await self._degraded(invocation, "response_pipeline_failed", finalization)
            return ProcessedResponse(result, final_text=guarded.text, finalization=finalization)

    async def _degraded(
        self,
        invocation: AgentInvocation,
        reason: str,
        finalization: ResponseFinalizationResult | None,
    ) -> None:
        await self._recorder.record_workflow_event(
            turn_id=invocation.turn_id,
            event_type=ItemType.RESPONSE_DEGRADED,
            payload={
                "artifact_id": finalization.final_output_artifact_id if finalization else None,
                "reason_code": reason,
                "fallback_type": "core_response",
            },
        )
