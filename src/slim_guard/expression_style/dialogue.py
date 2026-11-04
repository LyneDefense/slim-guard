"""Reviewed dialogue samples, separate from real user facts and chat history."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DialogueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


def validate_dialogue(messages: tuple[DialogueMessage, ...]) -> None:
    """A sample/history consists of complete exchanges, never system instructions."""
    if len(messages) % 2 or any(
        message.role != ("user" if index % 2 == 0 else "assistant")
        for index, message in enumerate(messages)
    ):
        raise ValueError("对话须按用户、医生交替排列，并以医生回复结束")
