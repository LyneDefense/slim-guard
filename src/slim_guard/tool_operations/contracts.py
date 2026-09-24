from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResult

OperationStatus = Literal["running", "unknown", "not_applied", "succeeded"]


class ToolOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    initial_execution_key: str
    tool_name: str
    tool_version: str
    context: ToolContext
    arguments: dict[str, Any]
    status: OperationStatus
    attempt: int
    result: ToolResult | None = None


class OperationClaim(BaseModel):
    operation: ToolOperation
    acquired: bool


class OperationStore(Protocol):
    async def reconcile_success(self, operation: ToolOperation, result: ToolResult) -> bool: ...

    async def start(
        self,
        *,
        tool_name: str,
        tool_version: str,
        context: ToolContext,
        arguments: dict[str, Any],
    ) -> OperationClaim: ...

    async def get(self, operation_id: str, *, context: ToolContext) -> ToolOperation | None: ...

    async def list_unsettled(
        self,
        *,
        user_id: str,
        thread_id: str,
        execution_mode: ToolExecutionMode,
    ) -> tuple[ToolOperation, ...]: ...

    async def acquire_retry(self, operation: ToolOperation) -> bool: ...

    async def finish(
        self,
        operation: ToolOperation,
        *,
        status: OperationStatus,
        result: ToolResult,
    ) -> None: ...
