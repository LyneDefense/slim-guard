from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable

from slim_guard.agent_models.errors import (
    FakeModelScriptExhausted,
    ModelGatewayClosed,
    ModelGatewayError,
)
from slim_guard.agent_models.gateway import ModelRequest, ModelResponse

ModelScriptStep = ModelResponse | ModelGatewayError


class ScriptedModelGateway:
    """Deterministic model replacement that consumes a predefined response script."""

    def __init__(self, steps: Iterable[ModelScriptStep]) -> None:
        self._steps = deque(steps)
        self._lock = asyncio.Lock()
        self._closed = False
        self.requests: list[ModelRequest] = []

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)

    @property
    def closed(self) -> bool:
        return self._closed

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async with self._lock:
            if self._closed:
                raise ModelGatewayClosed("Scripted model gateway is closed")
            self.requests.append(request)
            if not self._steps:
                raise FakeModelScriptExhausted(
                    f"Unexpected model call #{len(self.requests)}: script is exhausted"
                )
            step = self._steps.popleft()
        if isinstance(step, ModelGatewayError):
            raise step
        return step

    async def close(self) -> None:
        async with self._lock:
            self._closed = True

    def assert_exhausted(self) -> None:
        if self._steps:
            raise AssertionError(f"Model script has {len(self._steps)} unconsumed step(s)")
