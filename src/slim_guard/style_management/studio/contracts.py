"""Only public workbench requests; model and database state are server-owned."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from slim_guard.expression_style.dialogue import DialogueMessage, validate_dialogue
from slim_guard.expression_style.persona import PersonaContent


class StudioInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftInput(StudioInput):
    revision: int = Field(ge=1, strict=True)
    content: PersonaContent


class ContentInput(StudioInput):
    content: PersonaContent


class PreviewInput(ContentInput):
    user_input: str = Field(min_length=1, max_length=4000)
    history: tuple[DialogueMessage, ...] = Field(default=(), max_length=22)

    @field_validator("history")
    @classmethod
    def complete_history(cls, value: tuple[DialogueMessage, ...]) -> tuple[DialogueMessage, ...]:
        validate_dialogue(value)
        return value


class CheckInput(StudioInput):
    revision: int = Field(ge=1, strict=True)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")


class PublishInput(CheckInput):
    check_id: str = Field(min_length=1, max_length=36)


class RestoreInput(StudioInput):
    revision: int = Field(ge=1, strict=True)
    version_id: str = Field(min_length=1, max_length=128)
