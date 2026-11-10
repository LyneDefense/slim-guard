"""Model capabilities for the workbench; no user repositories or business tools."""

from typing import Any, Protocol

from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.persona import PersonaContent


class StudioModelError(RuntimeError):
    """A model call failed or did not produce a verifiable result."""


class StudioPreviewPort(Protocol):
    async def reply(
        self,
        *,
        style: StyleProfileSnapshot,
        user_input: str,
        history: tuple[DialogueMessage, ...],
    ) -> dict[str, Any]: ...


class StyleStudioModelPort(Protocol):
    @property
    def signature(self) -> str: ...

    async def preview(
        self,
        *,
        content: PersonaContent,
        user_input: str,
        history: tuple[DialogueMessage, ...],
    ) -> dict[str, Any]: ...

    async def check(self, *, content: PersonaContent) -> dict[str, Any]: ...

    async def organize(self, *, content: PersonaContent) -> dict[str, Any]: ...
