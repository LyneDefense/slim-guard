from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from slim_guard.harness.context import CompiledContext
from slim_guard.harness.failures import HarnessFailure, context_compilation_failure
from slim_guard.harness.initialization import (
    InitializedTurn,
    TurnInitializationRequest,
    TurnInitializer,
)
from slim_guard.harness.loop import HarnessLoopResult
from slim_guard.harness.preparation import (
    PreparationError,
    PreparedTurn,
    TurnContextPreparer,
    TurnGrants,
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.recall import MemoryRecallResult
from slim_guard.runtime.invocation.core_result import (
    CoreExecutionCancelled,
    CoreExecutionFailed,
)
from slim_guard.usage.context import usage_scope

if TYPE_CHECKING:
    from slim_guard.response_pipeline.contracts import ResponseFinalizationResult
    from slim_guard.response_pipeline.processor import ResponseProcessor
    from slim_guard.runtime.invocation.core import CoreInvocationRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TurnRunResult:
    initialized: InitializedTurn
    compiled: CompiledContext | None
    loop: HarnessLoopResult
    memory_recall: MemoryRecallResult | None = None
    response_finalization: ResponseFinalizationResult | None = None

    @property
    def final_text(self) -> str | None:
        return self.loop.final_text


class TurnHarness:
    """Own a Turn's lifecycle, not business routing or reply policy.

    Core returns a draft, the response processor returns user-visible messages,
    and only this owner finishes the Turn. Delivery remains outside this layer.
    """

    def __init__(
        self,
        *,
        initializer: TurnInitializer,
        preparation: TurnContextPreparer,
        core: CoreInvocationRunner,
        responses: ResponseProcessor,
        recorder: HarnessRunRecorder,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._initializer = initializer
        self._preparation = preparation
        self._core = core
        self._responses = responses
        self._recorder = recorder
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        *,
        request: TurnInitializationRequest,
        grants: TurnGrants | None = None,
    ) -> TurnRunResult:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("Harness Turn Runner clock must be timezone-aware")
        initialized = await self._initializer.initialize(request)
        with usage_scope(
            user_id=initialized.context.user_id,
            turn_id=initialized.turn.id,
            thread_id=initialized.thread.id,
        ):
            return await self._run_initialized(initialized, grants or TurnGrants(), now)

    async def _run_initialized(
        self, initialized: InitializedTurn, grants: TurnGrants, now: datetime
    ) -> TurnRunResult:
        prepared: PreparedTurn | None = None
        finalization = None
        recall = None
        result = HarnessLoopResult(
            termination=HarnessTermination.FATAL_ERROR,
            final_text=None,
            messages=(),
            model_responses=(),
            tool_outcomes=(),
        )
        try:
            prepared = await self._preparation.prepare(initialized, grants, now)
            recall = prepared.memory_recall
            executed = await self._core.run(initialized, prepared, now)
            result = executed.loop
            processed = await self._responses.process(
                initialized=initialized,
                invocation=executed.invocation,
                result=result,
                assessment=prepared.safety,
            )
            result, finalization = processed.loop, processed.finalization
        except PreparationError as exc:
            recall = exc.memory_recall
            result = replace(result, failure=context_compilation_failure(exc.error))
        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, (CoreExecutionCancelled, CoreExecutionFailed)):
                result = exc.result
            result = replace(
                result,
                termination=HarnessTermination.FATAL_ERROR,
                final_text=None,
                outgoing_messages=(),
                final_output_artifact_id=None,
                failure=result.failure
                or HarnessFailure(
                    code="execution_cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "turn_execution_failed",
                    error_type=type(exc).__name__,
                    retryable=isinstance(exc, asyncio.CancelledError),
                ),
            )
            # Preserve cancellation/original errors, even if persistence is unavailable.
            try:
                await self._finish(initialized, result)
            except Exception:
                logger.exception(
                    "turn_failure_record_failed", extra={"turn_id": initialized.turn.id}
                )
            if isinstance(exc, CoreExecutionFailed):
                raise exc.error from None
            raise
        # Never retry finish_run blindly: it may already have persisted messages.
        await self._finish(initialized, result)
        return TurnRunResult(
            initialized=initialized,
            compiled=prepared.compiled if prepared else None,
            loop=result,
            memory_recall=recall,
            response_finalization=finalization,
        )

    async def _finish(self, initialized: InitializedTurn, result: HarnessLoopResult) -> None:
        await self._recorder.finish_run(
            turn_id=initialized.turn.id,
            termination=result.termination,
            final_text=result.final_text,
            model_call_count=result.model_call_count,
            tool_call_count=result.tool_call_count,
            total_token_count=result.total_token_count,
            failure=result.failure,
            outgoing_messages=result.outgoing_messages,
        )
