"""Structured output contract for user-visible conversation planning."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from slim_guard.runtime.contracts import ContractModel

OperationNoticeKind = Literal["operation_result", "operation_clarification", "operation_progress"]


class ConversationMessageProposal(ContractModel):
    """One ordered text message proposed for a group-chat participant."""

    participant: Literal["system_assistant", "coach"]
    text: str = Field(min_length=1, max_length=4000)
    source_refs: tuple[str, ...] = Field(default=(), max_length=64)
    requires_response: bool = False
    notice_text: str | None = Field(default=None, min_length=1, max_length=800)
    notice_kind: OperationNoticeKind | None = None

    @model_validator(mode="after")
    def system_notice_only(self) -> ConversationMessageProposal:
        if self.participant == "coach":
            if (
                self.requires_response
                or self.notice_text is not None
                or self.notice_kind is not None
            ):
                raise ValueError("Notice metadata belongs to the system assistant")
        else:
            if self.notice_kind is None:
                raise ValueError("System notice must declare its operation purpose")
            if "requires_response" not in self.model_fields_set:
                raise ValueError("System notice must explicitly declare requires_response")
            if self.requires_response != (self.notice_kind == "operation_clarification"):
                raise ValueError("Only operation clarification notices require a response")
        if self.notice_text is not None and not self.notice_text.strip():
            raise ValueError("Notice text cannot be blank")
        return self

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

    schema_version: Literal["3"] = "3"
    messages: tuple[ConversationMessageProposal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_optional_system_notice(self) -> ConversationDecision:
        system = [message for message in self.messages if message.participant == "system_assistant"]
        if len(system) > 1:
            raise ValueError("Combine operation notices into at most one system notification")
        return self


__all__ = ["ConversationDecision", "ConversationMessageProposal"]
