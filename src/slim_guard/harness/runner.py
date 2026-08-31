from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.harness.context import CompiledContext, ContextCompiler
from slim_guard.harness.context_data import ContextDataProvider, EmptyContextDataProvider
from slim_guard.harness.errors import ContextCompilationError
from slim_guard.harness.failures import context_compilation_failure
from slim_guard.harness.initialization import (
    InitializedTurn,
    TurnInitializationRequest,
    TurnInitializer,
)
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.loop import HarnessLoop, HarnessLoopResult
from slim_guard.harness.safety import (
    DefaultInputSafetyPolicy,
    InputSafetyPolicy,
    OutputGuard,
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallRunner
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.tools.policy import ToolAuthorization


class HarnessTurnGrants(BaseModel):
    """Trusted per-run grants; the model never supplies this object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tool_names: tuple[str, ...] | None = None
    confirmed_execution_keys: frozenset[str] = frozenset()
    reviewed_execution_keys: frozenset[str] = frozenset()
    isolated_write_environment: bool = False


@dataclass(frozen=True, slots=True)
class HarnessTurnRunResult:
    initialized: InitializedTurn
    compiled: CompiledContext | None
    loop: HarnessLoopResult

    @property
    def final_text(self) -> str | None:
        return self.loop.final_text


class HarnessTurnRunner:
    """Application-level entry point for one new durable Agent Turn."""

    def __init__(
        self,
        *,
        initializer: TurnInitializer,
        compiler: ContextCompiler,
        model: ModelGateway,
        tool_calls: ToolCallRunner,
        recorder: HarnessRunRecorder,
        limits: HarnessLimits,
        context_data: ContextDataProvider | None = None,
        input_safety: InputSafetyPolicy | None = None,
        output_guard: OutputGuard | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._initializer = initializer
        self._compiler = compiler
        self._recorder = recorder
        self._context_data = context_data or EmptyContextDataProvider()
        self._input_safety = input_safety or DefaultInputSafetyPolicy()
        self._clock = clock or self._utc_now
        self._loop = HarnessLoop(
            model=model,
            tool_calls=tool_calls,
            recorder=recorder,
            limits=limits,
            output_guard=output_guard,
            clock=self._clock,
        )

    async def run(
        self,
        *,
        request: TurnInitializationRequest,
        grants: HarnessTurnGrants | None = None,
    ) -> HarnessTurnRunResult:
        current_time = self._clock()
        if current_time.utcoffset() is None:
            raise ValueError("Harness Turn Runner clock must be timezone-aware")
        initialized = await self._initializer.initialize(request)
        active_grants = grants or HarnessTurnGrants()
        safety_assessment = self._input_safety.assess(initialized.input_items)
        try:
            authoritative_context = dict(
                await self._context_data.load(
                    user_id=initialized.context.user_id,
                    current_time=current_time,
                    trigger=initialized.turn.trigger,
                    input_items=initialized.input_items,
                )
            )
            if safety_assessment.blocks_tools:
                authoritative_context["health_safety"] = safety_assessment.to_context()
            allowed_tool_names = (
                () if safety_assessment.blocks_tools else active_grants.allowed_tool_names
            )
            compiled = self._compiler.compile(
                initialized=initialized,
                current_time=current_time,
                allowed_tool_names=allowed_tool_names,
                authoritative_context=authoritative_context,
            )
        except (ContextCompilationError, ValueError, TypeError) as exc:
            compilation_error = (
                exc
                if isinstance(exc, ContextCompilationError)
                else ContextCompilationError("Trusted context data is invalid")
            )
            failure = context_compilation_failure(compilation_error)
            await self._recorder.finish_run(
                turn_id=initialized.turn.id,
                termination=HarnessTermination.FATAL_ERROR,
                final_text=None,
                model_call_count=0,
                tool_call_count=0,
                total_token_count=0,
                failure=failure,
            )
            return HarnessTurnRunResult(
                initialized=initialized,
                compiled=None,
                loop=HarnessLoopResult(
                    termination=HarnessTermination.FATAL_ERROR,
                    final_text=None,
                    messages=(),
                    model_responses=(),
                    tool_outcomes=(),
                    failure=failure,
                ),
            )

        authorization = ToolAuthorization(
            allowed_tool_names=frozenset(compiled.allowed_tool_names),
            confirmed_execution_keys=active_grants.confirmed_execution_keys,
            reviewed_execution_keys=active_grants.reviewed_execution_keys,
            isolated_write_environment=active_grants.isolated_write_environment,
        )
        await self._recorder.record_context_snapshot(
            turn_id=initialized.turn.id,
            payload={
                "compiled_at": current_time.isoformat(),
                "request": compiled.request.model_dump(mode="json"),
                "allowed_tool_names": list(compiled.allowed_tool_names),
                "input_item_ids": list(compiled.input_item_ids),
                "authorization": {
                    "allowed_tool_names": sorted(authorization.allowed_tool_names),
                    "confirmed_execution_keys": sorted(
                        authorization.confirmed_execution_keys
                    ),
                    "reviewed_execution_keys": sorted(
                        authorization.reviewed_execution_keys
                    ),
                    "isolated_write_environment": (
                        authorization.isolated_write_environment
                    ),
                },
            },
        )
        loop_result = await self._loop.run(
            request=compiled.request,
            context=initialized.context,
            authorization=authorization,
            source_item_id=initialized.source_item_id,
            now=current_time,
            safety_assessment=safety_assessment,
        )
        return HarnessTurnRunResult(
            initialized=initialized,
            compiled=compiled,
            loop=loop_result,
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
