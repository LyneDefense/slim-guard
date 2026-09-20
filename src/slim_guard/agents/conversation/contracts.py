"""Structured output contract for user-visible conversation planning."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from slim_guard.runtime.contracts import ContractModel


class ConversationDecision(ContractModel):
    """Partition a semantic result into system and optional coach messages.

    The model proposes wording and provenance only.  The Turn Harness remains
    authoritative for tool status, cards, permissions, and safety.
    """

    schema_version: Literal["1"] = "1"
    system_text: str = Field(min_length=1, max_length=4000)
    coach_text: str | None = Field(default=None, max_length=2000)
    coach_source_refs: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("coach_source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Coach source references cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Coach source references must be unique")
        return value


__all__ = ["ConversationDecision"]
