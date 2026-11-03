"""Core ends orchestration with a handoff, never a user-facing reply draft."""

from pydantic import Field, field_validator

from slim_guard.runtime.contracts import ContractModel


class OperationQuestion(ContractModel):
    """A missing business input, not a conversational question or operation result."""

    text: str = Field(min_length=1, max_length=800)
    source_refs: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Operation question cannot be blank")
        return value.strip()


class CoreCompletion(ContractModel):
    """Actual facts stay in inputs/receipts; Core does not summarize them into prose."""

    operation_question: OperationQuestion | None = None
    failed_operation_refs: tuple[str, ...] = Field(
        default=(),
        max_length=64,
        description="尚未进入实际执行、且本轮最终仍未解决的失败调用 ID；已修复的参数尝试不在此列",
    )
