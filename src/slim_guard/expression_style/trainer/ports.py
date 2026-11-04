"""Trainer depends on this durable job port, never ORM or private repositories."""

from typing import Any, Protocol

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.expression_style.contracts import StyleProfileSnapshot

from .contracts import TestCase


class BuildInterrupted(RuntimeError):
    """The worker lost its lease or the user cancelled the build."""


class BuildPort(Protocol):
    async def load(self, key: str) -> Any: ...
    async def save(self, key: str, value: Any, *, stage: str, message: str) -> None: ...
    async def event(self, stage: str, message: str, **details: Any) -> None: ...
    async def record_call(self) -> dict[str, int]: ...
    async def record_tokens(self, tokens: int) -> None: ...


class CoachPreviewPort(Protocol):
    @property
    def signature(self) -> str: ...

    async def run(
        self,
        *,
        case: TestCase,
        style: StyleProfileSnapshot,
        feedback: tuple[dict[str, object], ...] = (),
    ) -> dict[str, Any]: ...


class CoachPreviewFactory(Protocol):
    def __call__(
        self, gateway: ModelGateway, model: str, request_seconds: int
    ) -> CoachPreviewPort: ...
