"""User-visible messages for the user/coach/system-assistant group chat."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParticipantRole(StrEnum):
    USER = "user"
    COACH = "coach"
    SYSTEM_ASSISTANT = "system_assistant"


class RenderKind(StrEnum):
    TEXT = "text"
    CARD = "card"
    IMAGE = "image"


class CardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    card_type: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(default="1", min_length=1, max_length=32)
    status: str = Field(default="success", min_length=1, max_length=32)
    data: dict[str, Any] = Field(default_factory=dict)
    source_execution_id: str | None = Field(default=None, min_length=1, max_length=128)


class ChatMessage(BaseModel):
    """One durable, user-visible message block.

    A system assistant message may be natural language or a card. A coach message
    is always text and is the only participant that enters Style Agent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0, strict=True)
    participant: ParticipantRole
    kind: RenderKind
    text: str | None = None
    card: CardPayload | None = None
    semantic_block_id: str | None = Field(default=None, max_length=128)
    source_refs: tuple[str, ...] = ()
    style_profile_version: str | None = Field(default=None, max_length=128)
    created_at: datetime

    @model_validator(mode="after")
    def validate_render(self) -> ChatMessage:
        if self.participant is ParticipantRole.USER and self.kind is RenderKind.CARD:
            raise ValueError("User messages cannot render cards")
        if self.participant is ParticipantRole.COACH and self.kind is not RenderKind.TEXT:
            raise ValueError("Coach messages must be text")
        if self.kind is RenderKind.TEXT and not (self.text and self.text.strip()):
            raise ValueError("Text messages require non-empty text")
        if self.kind is RenderKind.CARD:
            if self.participant is not ParticipantRole.SYSTEM_ASSISTANT:
                raise ValueError("Cards belong to the system assistant")
            if self.card is None:
                raise ValueError("Card messages require card payload")
        if self.kind is RenderKind.IMAGE and not self.text and self.card is not None:
            raise ValueError("Image messages cannot carry card payload")
        return self


__all__ = ["CardPayload", "ChatMessage", "ParticipantRole", "RenderKind"]
