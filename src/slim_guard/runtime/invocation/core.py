"""Invocation lifecycle for the tool-capable Core (not the zero-tool typed runner)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.loop import HarnessLoopResult, LoopProgress
from slim_guard.harness.preparation import PreparedTurn
from slim_guard.harness.termination import HarnessTermination
from slim_guard.observability.tracing import current_trace_id
from slim_guard.runtime.contracts import (
    AgentArtifact,
    AgentInvocation,
    AgentResult,
    AgentRole,
    ArtifactProducerRole,
    InvocationStatus,
)
from slim_guard.runtime.invocation.core_result import (
    CoreExecutionCancelled,
    CoreExecutionFailed,
    CoreExecutionResult,
)
from slim_guard.runtime.invocation.store import InvocationStore

if TYPE_CHECKING:
    from slim_guard.agents.core import CoreAgent

logger = logging.getLogger(__name__)


class CoreInvocationRunner:
    def __init__(
        self,
        *,
        agent: CoreAgent,
        store: InvocationStore | None = None,
        graph_version: str = "core-primary-v1",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._agent = agent
        self._store = store
        self._graph_version = graph_version
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self, initialized: InitializedTurn, prepared: PreparedTurn, now: datetime
    ) -> CoreExecutionResult:
        compiled = prepared.compiled
        invocation = AgentInvocation(
            invocation_id=f"inv-{uuid4()}",
            trace_id=current_trace_id() or initialized.turn.id,
            thread_id=initialized.thread.id,
            turn_id=initialized.turn.id,
            graph_version=self._graph_version,
            agent_role=AgentRole.CORE,
            agent_version=initialized.turn.agent_version_id,
            caller="turn_harness",
            input_schema="CompiledContext",
            allowed_tools=tuple(compiled.allowed_tool_names),
            privacy_scopes=("current_user_input", "working_memory", "profile"),
            deadline_at=initialized.turn.deadline_at or now + timedelta(seconds=120),
            payload={
                "input_item_ids": list(compiled.input_item_ids),
                "context_evidence_ids": list(compiled.evidence_item_ids),
            },
        )
        if self._store is not None:
            await self._store.start_invocation(
                invocation, reason_summary="理解本轮任务并调用获准的业务或专业能力", started_at=now
            )
        progress = LoopProgress()
        try:
            result = await self._agent.run(
                request=compiled.request,
                context=replace(initialized.context, agent_invocation_id=invocation.invocation_id),
                authorization=prepared.authorization,
                source_item_id=initialized.source_item_id,
                now=now,
                trusted_evidence_item_ids=compiled.evidence_item_ids,
                progress=progress,
            )
        except asyncio.CancelledError as exc:
            result = progress.interrupted(exc, cancelled=True)
            try:
                await self._complete(invocation, result)
            except Exception:
                logger.exception("core_cancellation_record_failed")
            raise CoreExecutionCancelled(result) from exc
        except Exception as exc:
            result = progress.interrupted(exc)
            try:
                await self._complete(invocation, result)
            except Exception:
                logger.exception("core_failure_record_failed")
            raise CoreExecutionFailed(result, exc) from exc
        try:
            result = await self._complete(invocation, result)
        except asyncio.CancelledError as exc:
            # Do not repeat a possibly committed artifact/completion write.
            raise CoreExecutionCancelled(progress.interrupted(exc, cancelled=True)) from exc
        except Exception as exc:
            raise CoreExecutionFailed(progress.interrupted(exc), exc) from exc
        return CoreExecutionResult(invocation, result)

    async def _complete(
        self, invocation: AgentInvocation, result: HarnessLoopResult
    ) -> HarnessLoopResult:
        artifact_id = None
        if result.final_text is not None:
            # This is the actual Core draft, never the styled/final reply.
            artifact = AgentArtifact.create(
                artifact_id=f"artifact-{uuid4()}",
                turn_id=invocation.turn_id,
                producer_role=ArtifactProducerRole.CORE,
                artifact_type="core_response",
                schema_version="1",
                payload={"text": result.final_text},
                created_at=self._clock(),
            )
            artifact_id = artifact.artifact_id
            if self._store is not None:
                await self._store.append_artifact(artifact, invocation_id=invocation.invocation_id)
        status = (
            InvocationStatus.SUCCEEDED
            if result.termination is HarnessTermination.FINAL_RESPONSE
            else InvocationStatus.FAILED
            if result.termination
            in {HarnessTermination.FATAL_ERROR, HarnessTermination.DEADLINE_EXCEEDED}
            else InvocationStatus.DEGRADED
        )
        failure_code = (
            result.failure.code
            if result.failure is not None
            else result.termination.value
            if status is InvocationStatus.FAILED
            else None
        )
        if self._store is not None:
            await self._store.complete_invocation(
                AgentResult(
                    invocation_id=invocation.invocation_id,
                    status=status,
                    output_schema="CoreResponse",
                    output_schema_version="1",
                    artifact_id=artifact_id,
                    model_call_count=len(result.model_responses),
                    tool_call_count=len(result.tool_outcomes),
                    token_usage=sum(item.usage.total_tokens for item in result.model_responses),
                    failure_code=failure_code,
                ),
                completed_at=self._clock(),
            )
        return replace(result, core_output_artifact_id=artifact_id)
