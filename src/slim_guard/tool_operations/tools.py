from pydantic import Field

from slim_guard.tools.contracts import ToolArguments, ToolEffectLevel
from slim_guard.tools.registry import RegisteredTool

LIST_OPERATIONS = "list_unsettled_tool_operations"
GET_OPERATION = "get_tool_operation"
RESUME_OPERATION = "resume_tool_operation"
RECOVERY_TOOL_NAMES = frozenset({LIST_OPERATIONS, GET_OPERATION, RESUME_OPERATION})


class OperationReference(ToolArguments):
    operation_id: str = Field(min_length=1, max_length=128)


class ListOperations(ToolArguments):
    pass


def operation_tool_definitions() -> tuple[RegisteredTool, ...]:
    descriptions = {
        LIST_OPERATIONS: "List this user's unfinished write operations in this conversation. "
        "Use explicit operation references, not similarity, to recover earlier work.",
        GET_OPERATION: "Inspect a saved write by operation_id; where supported, reconcile "
        "with authoritative storage. Unknown or running is not evidence of no write.",
        RESUME_OPERATION: "Recover an explicitly referenced operation without new business "
        "arguments. Replays confirmed success; retries only a retryable, confirmed-not-applied "
        "attempt using its original command. Does not retry unknown writes. Current grants "
        "for the original capability and operation are required.",
    }
    return tuple(
        RegisteredTool(
            name=name,
            description=description,
            version="v1",
            arguments_model=ListOperations if name == LIST_OPERATIONS else OperationReference,
            # The resume dispatcher isn't a business-write grant. The original
            # tool's effect policy is checked independently before execution.
            effect_level=(
                ToolEffectLevel.REVERSIBLE_WRITE
                if name == RESUME_OPERATION
                else ToolEffectLevel.READ
            ),
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=300,
        )
        for name, description in descriptions.items()
    )
