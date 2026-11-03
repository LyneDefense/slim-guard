"""Optional observation boundary; model execution never depends on storage adapters."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from slim_guard.agent_models.gateway import ModelRequest
    from slim_guard.agent_models.vision import VisionInspectionRequest


class ModelExchangeObserver(Protocol):
    def observe(
        self, call_id: str, request: ModelRequest | VisionInspectionRequest
    ) -> AbstractAsyncContextManager[dict[str, Any]]: ...
