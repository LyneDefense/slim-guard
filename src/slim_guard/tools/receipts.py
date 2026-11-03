"""Fold operation receipts without treating a successful status query as a successful write."""

from slim_guard.tools.contracts import (
    ToolExecution,
    ToolOperationReceipt,
    ToolResult,
)


def effective_executions(executions: tuple[ToolExecution, ...]) -> tuple[ToolExecution, ...]:
    folded: dict[str, ToolExecution] = {}
    for execution in executions:
        result = execution.result
        receipt = result.operation
        identity = receipt.operation_id if receipt else execution.tool_call_id
        if identity in folded:
            execution = execution.model_copy(
                update={"mutation_kind": folded[identity].mutation_kind}
            )
        folded[identity] = execution
        view = result.output.get("operation")
        if not isinstance(view, dict):
            continue
        original = folded.get(str(view.get("operation_id")))
        if original is None:
            continue
        original_name = (
            original.result.operation.tool_name if original.result.operation else original.tool_name
        )
        if view.get("tool_name") != original_name:
            continue
        status = view.get("status")
        if status not in {"running", "unknown", "not_applied", "succeeded"}:
            continue
        resolved = original.result
        if isinstance(view.get("result"), dict):
            resolved = ToolResult.model_validate(view["result"])
        resolved = resolved.model_copy(
            update={
                "operation": ToolOperationReceipt(
                    operation_id=str(view["operation_id"]),
                    tool_name=original_name,
                    status=status,
                )
            }
        )
        folded[str(view["operation_id"])] = original.model_copy(update={"result": resolved})
    return tuple(folded.values())
