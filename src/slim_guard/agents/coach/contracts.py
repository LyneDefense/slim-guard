"""Direct coach expression and evidence-grounding contracts, without a prose draft."""

from typing import Any

from pydantic import Field, field_validator

from slim_guard.runtime.contracts import ContractModel


class CoachEvidence(ContractModel):
    context: dict[str, Any]
    observations: tuple[dict[str, Any], ...] = ()
    assessments: tuple[dict[str, Any], ...] = ()
    allowed_source_refs: tuple[str, ...] = ()
    operation_question: str | None = None


class CoachReply(ContractModel):
    text: str = Field(min_length=1, max_length=6000)
    source_refs: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Coach reply cannot be blank")
        return value.strip()

    @field_validator("source_refs")
    @classmethod
    def unique_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref.strip() for ref in value) or len(value) != len(set(value)):
            raise ValueError("Source references must be nonblank and unique")
        return value
