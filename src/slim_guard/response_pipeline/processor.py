"""Own output safety, finalization adoption and fallback outside the Core loop."""

import logging
from dataclasses import dataclass

from slim_guard.group_chat.contracts import ChatMessage
from slim_guard.harness.events import ItemType
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.loop import HarnessLoopResult
from slim_guard.harness.safety import OutputGuard, PermissiveOutputGuard
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.response_pipeline.contracts import (
    ResponseFinalizationRequest,
    ResponseFinalizationResult,
    ResponseFinalizer,
)
from slim_guard.runtime.contracts import AgentInvocation
from slim_guard.usage.context import usage_scope

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
    ) -> None:
        self._recorder = recorder
        self._finalizer = finalizer
        self._guard = output_guard or PermissiveOutputGuard()

    async def process(
        self,
        *,
        initialized: InitializedTurn,
        invocation: AgentInvocation,
        result: HarnessLoopResult,
    ) -> ProcessedResponse:
        if result.termination is not HarnessTermination.FINAL_RESPONSE:
            return ProcessedResponse(result, final_text=None)
        assert result.final_text is not None
        guarded = self._guard.review(
            text=result.final_text, tool_outcomes=result.tool_outcomes
        )
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
                    )
                )
            checked = self._guard.review(
                text=finalization.text, tool_outcomes=result.tool_outcomes
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
