"""Core Agent implementation.

The Core Agent owns task understanding and the model/tool loop.  Turn-level
persistence, safety gates, budgets, final adoption, and delivery remain the
responsibility of the outer Turn Harness.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from slim_guard.agent_models.gateway import ModelGateway, ModelRequest
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.loop import (
    BeforeFinishHook,
    HarnessLoop,
    HarnessLoopResult,
    HarnessTurnContext,
    ResponsePipelineHook,
)
from slim_guard.harness.safety import OutputGuard, SafetyAssessment
from slim_guard.harness.tool_calls import ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.tools.policy import ToolAuthorization


class CoreAgent:
    """Run the primary user-facing reasoning and tool-use loop for one Turn.

    This boundary is intentionally behavior-preserving while the old dual-path
    workflow is removed in later phases.  It gives the main coach a stable owner
    without making the Turn Harness itself a business Agent.
    """

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        limits: HarnessLimits,
        recorder: HarnessRunRecorder,
        output_guard: OutputGuard | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._loop = HarnessLoop(
            model=model,
            tool_calls=tool_calls,
            recorder=recorder,
            limits=limits,
            output_guard=output_guard,
            clock=clock,
        )

    async def run(
        self,
        *,
        request: ModelRequest,
        context: HarnessTurnContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
        trusted_evidence_item_ids: tuple[str, ...] = (),
        safety_assessment: SafetyAssessment | None = None,
        response_pipeline_hook: ResponsePipelineHook | None = None,
        before_finish_hook: BeforeFinishHook | None = None,
    ) -> HarnessLoopResult:
        """Execute one bounded Core Agent invocation."""

        return await self._loop.run(
            request=request,
            context=context,
            authorization=authorization,
            source_item_id=source_item_id,
            now=now,
            trusted_evidence_item_ids=trusted_evidence_item_ids,
            safety_assessment=safety_assessment,
            response_pipeline_hook=response_pipeline_hook,
            before_finish_hook=before_finish_hook,
        )
