"""Prepare trusted context and effective permissions before Core executes."""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from slim_guard.harness.context import CompiledContext, ContextCompiler
from slim_guard.harness.context_data import ContextDataProvider, EmptyContextDataProvider
from slim_guard.harness.errors import ContextCompilationError
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.safety import DefaultInputSafetyPolicy, InputSafetyPolicy, SafetyAssessment
from slim_guard.harness.trace import HarnessRunRecorder
from slim_guard.memory.recall import MemoryRecaller, MemoryRecallResult
from slim_guard.tools.policy import ToolAuthorization


class TurnGrants(BaseModel):
    """Trusted per-run grants; never supplied by the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tool_names: tuple[str, ...] | None = None
    confirmed_execution_keys: frozenset[str] = frozenset()
    reviewed_execution_keys: frozenset[str] = frozenset()
    isolated_write_environment: bool = False


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    compiled: CompiledContext
    authorization: ToolAuthorization
    safety: SafetyAssessment
    memory_recall: MemoryRecallResult | None


class PreparationError(ContextCompilationError):
    def __init__(self, error: ContextCompilationError, recall: MemoryRecallResult | None):
        super().__init__(str(error))
        self.error = error
        self.memory_recall = recall


class TurnContextPreparer:
    """Coordinate existing loading/recall/compiler policies, without executing Agents."""

    def __init__(
        self,
        *,
        compiler: ContextCompiler,
        recorder: HarnessRunRecorder,
        context_data: ContextDataProvider | None = None,
        memory_recaller: MemoryRecaller | None = None,
        input_safety: InputSafetyPolicy | None = None,
    ) -> None:
        self._compiler = compiler
        self._recorder = recorder
        self._context_data = context_data or EmptyContextDataProvider()
        self._memory_recaller = memory_recaller
        self._input_safety = input_safety or DefaultInputSafetyPolicy()

    async def prepare(
        self, initialized: InitializedTurn, grants: TurnGrants, now: datetime
    ) -> PreparedTurn:
        recall = None
        try:
            safety = self._input_safety.assess(initialized.input_items)
            context = dict(
                await self._context_data.load(
                    user_id=initialized.context.user_id,
                    current_time=now,
                    trigger=initialized.turn.trigger,
                    input_items=initialized.input_items,
                )
            )
            if self._memory_recaller is not None:
                recall = await self._memory_recaller.recall(
                    initialized=initialized, current_time=now, context=context
                )
                context = recall.context
            if safety.code != "none":
                context["health_safety"] = safety.to_context()
            compiled = self._compiler.compile(
                initialized=initialized,
                current_time=now,
                allowed_tool_names=() if safety.blocks_tools else grants.allowed_tool_names,
                authoritative_context=context,
            )
        except (ContextCompilationError, ValueError, TypeError) as exc:
            error = (
                exc
                if isinstance(exc, ContextCompilationError)
                else ContextCompilationError("Trusted context data is invalid")
            )
            raise PreparationError(error, recall) from exc
        authorization = ToolAuthorization(
            allowed_tool_names=frozenset(compiled.allowed_tool_names),
            confirmed_execution_keys=grants.confirmed_execution_keys,
            reviewed_execution_keys=grants.reviewed_execution_keys,
            isolated_write_environment=grants.isolated_write_environment,
        )
        await self._recorder.record_context_snapshot(
            turn_id=initialized.turn.id,
            payload={
                "compiled_at": now.isoformat(),
                "request": compiled.request.model_dump(mode="json"),
                "allowed_tool_names": list(compiled.allowed_tool_names),
                "input_item_ids": list(compiled.input_item_ids),
                "authorization": {
                    "allowed_tool_names": sorted(authorization.allowed_tool_names),
                    "confirmed_execution_keys": sorted(authorization.confirmed_execution_keys),
                    "reviewed_execution_keys": sorted(authorization.reviewed_execution_keys),
                    "isolated_write_environment": authorization.isolated_write_environment,
                },
            },
        )
        return PreparedTurn(compiled, authorization, safety, recall)
