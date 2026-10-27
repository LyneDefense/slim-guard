"""Structured output contract for user-visible conversation planning."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from slim_guard.runtime.contracts import ContractModel


class ConversationMessageProposal(ContractModel):
    """One ordered text message proposed for a group-chat participant."""

    participant: Literal["system_assistant", "coach"]
    text: str = Field(min_length=1, max_length=4000)
    source_refs: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Conversation message text cannot be blank")
        return value.strip()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Message source references cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Message source references must be unique")
        return value


class ConversationDecision(ContractModel):
    """Propose an ordered assistant/coach message sequence, never tool cards."""

    schema_version: Literal["2"] = "2"
    messages: tuple[ConversationMessageProposal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_system_assistant_message(self) -> ConversationDecision:
        if sum(message.participant == "system_assistant" for message in self.messages) != 1:
            raise ValueError("Conversation plan must contain exactly one system assistant message")
        return self


__all__ = ["ConversationDecision", "ConversationMessageProposal"]
