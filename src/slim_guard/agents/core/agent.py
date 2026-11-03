"""Core Agent implementation.

The Core Agent owns task understanding and the model/tool loop.  Turn-level
persistence, final reply processing, and delivery belong to their outer layers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from slim_guard.agent_models.gateway import ModelGateway, ModelRequest
from slim_guard.agents.core.contracts import CoreCompletion
from slim_guard.harness.loop import (
    HarnessLoop,
    HarnessLoopResult,
    HarnessTurnContext,
    LoopProgress,
)
from slim_guard.harness.tool_calls import ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.tools.policy import ToolAuthorization


class CoreAgent:
    """Run business orchestration and tool use for one Turn, without reply prose.

    Business decisions belong to the model; this Agent does not finalize the
    Turn, render group-chat replies, or deliver user messages.
    """

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        recorder: HarnessRunRecorder,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._loop = HarnessLoop(
            model=model,
            tool_calls=tool_calls,
            recorder=recorder,
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
        progress: LoopProgress | None = None,
    ) -> HarnessLoopResult:
        """Execute one bounded Core Agent invocation."""

        result = await self._loop.run(
            request=request,
            context=context,
            authorization=authorization,
            source_item_id=source_item_id,
            now=now,
            trusted_evidence_item_ids=trusted_evidence_item_ids,
            progress=progress,
            final_output_type=CoreCompletion,
        )
        if result.final_output is not None and source_item_id:
            completion = CoreCompletion.model_validate(result.final_output)
            question = completion.operation_question
            if question is not None:
                refs = tuple(
                    dict.fromkeys(
                        source_item_id if ref == "current-user-message" else ref
                        for ref in question.source_refs
                    )
                )
                completion = completion.model_copy(
                    update={"operation_question": question.model_copy(update={"source_refs": refs})}
                )
            result = replace(result, final_output=completion.model_dump(mode="json"))
        return result
