"""Best-effort accounting only: never changes a model request or rejects its result."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from slim_guard.agent_models.gateway import ModelGateway, ModelRequest, ModelResponse
    from slim_guard.agent_models.vision import (
        VisionInspectionRequest,
        VisionInspectionResponse,
        VisionModelGateway,
    )
from slim_guard.usage.context import context_fields
from slim_guard.usage.ports import UsageStore

logger = logging.getLogger(__name__)
_RAW: ContextVar[dict[str, Any] | None] = ContextVar("provider_usage", default=None)


def capture_provider_usage(body: Any) -> None:
    """Call before content parsing, so invalid JSON/tool arguments still get billed usage."""
    target = _RAW.get()
    if target is None or not isinstance(body, dict):
        return
    target["_captured"] = True
    request_id = body.get("request_id") or body.get("id")
    if isinstance(request_id, str):
        target["provider_request_id"] = request_id[:256]
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return
    fields = {
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "total_tokens": usage.get("total_tokens"),
        "cached_input_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        if isinstance(usage.get("prompt_tokens_details", {}), dict)
        else None,
    }
    for key, value in fields.items():
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            target[key] = value
    if "total_tokens" not in target and all(k in target for k in ("input_tokens", "output_tokens")):
        target["total_tokens"] = target["input_tokens"] + target["output_tokens"]


class MeasuredModelGateway:
    def __init__(self, gateway: ModelGateway, store: UsageStore, provider: str = "zhipu") -> None:
        self._gateway, self._store, self._provider = gateway, store, provider

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async with ModelCall(
            self._store,
            self._provider,
            request.model,
            request.output_schema_name or request.purpose.value,
        ) as call:
            result = await self._gateway.complete(request)
            call.result(result)
            return result

    async def close(self) -> None:
        await self._gateway.close()


class MeasuredVisionGateway:
    def __init__(
        self, gateway: VisionModelGateway, store: UsageStore, provider: str = "zhipu"
    ) -> None:
        self._gateway, self._store, self._provider = gateway, store, provider

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        async with ModelCall(
            self._store, self._provider, request.model, "vision_inspection"
        ) as call:
            result = await self._gateway.inspect(request)
            call.result(result)
            return result

    async def close(self) -> None:
        await self._gateway.close()


class ModelCall:
    def __init__(self, store: UsageStore, provider: str, model: str, purpose: str) -> None:
        self.store = store
        self.id = str(uuid4())
        self.values = {
            **context_fields(),
            "id": self.id,
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "started_at": datetime.now(UTC),
        }
        self.raw: dict[str, Any] = {}
        self.started = monotonic()

    async def _write(self, method: str, *args: Any) -> None:
        try:
            # A broken accounting DB must not indefinitely delay model execution.
            async with asyncio.timeout(2):
                await getattr(self.store, method)(*args)
        except Exception:
            logger.exception("model_usage_write_failed", extra={"call_id": self.id})

    async def __aenter__(self) -> ModelCall:
        await self._write("start", self.values)
        self.token = _RAW.set(self.raw)
        return self

    def result(self, response: Any) -> None:
        # Custom/test providers may expose normalized usage without raw capture.
        usage = getattr(response, "usage", None)
        if usage is not None and not self.raw.get("_captured"):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                if key in usage.model_fields_set:
                    self.raw[key] = getattr(usage, key)
        if not self.raw.get("provider_request_id"):
            request_id = getattr(response, "provider_request_id", None)
            self.raw["provider_request_id"] = (
                request_id[:256] if isinstance(request_id, str) else None
            )
        if "total_tokens" not in self.raw and all(
            k in self.raw for k in ("input_tokens", "output_tokens")
        ):
            self.raw["total_tokens"] = self.raw["input_tokens"] + self.raw["output_tokens"]

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        _RAW.reset(self.token)
        await self._write(
            "finish",
            self.id,
            {
                **{key: value for key, value in self.raw.items() if not key.startswith("_")},
                "status": "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "failed"
                if exc
                else "succeeded",
                "error_type": type(exc).__name__ if exc else None,
                "completed_at": datetime.now(UTC),
                "duration_ms": max(0, int((monotonic() - self.started) * 1000)),
            },
        )
