"""Bounded, audited model I/O snapshots, correlated with the actual usage attempt.

Only normalized messages/results are captured: no provider envelope, credentials,
image bytes, metadata, or hidden reasoning. Recording never changes execution.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from slim_guard.agent_models.gateway import ModelRequest, ModelResponse
from slim_guard.agent_models.vision import VisionInspectionRequest, VisionInspectionResponse
from slim_guard.observability.tracing import (
    InteractionTraceRepository,
    TraceSpanRef,
    current_trace_id,
)
from slim_guard.usage.context import context_fields

logger = logging.getLogger(__name__)


def bounded(value: Any, remaining: list[int]) -> Any:
    if isinstance(value, str):
        limit = max(0, remaining[0])
        remaining[0] -= len(value)
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, dict):
        return {key: bounded(item, remaining) for key, item in value.items()}
    if isinstance(value, list):
        return [bounded(item, remaining) for item in value]
    return value


class ModelExchangeRecorder:
    def __init__(self, traces: InteractionTraceRepository) -> None:
        self._traces = traces

    @asynccontextmanager
    async def observe(
        self, call_id: str, request: ModelRequest | VisionInspectionRequest
    ) -> AsyncIterator[dict[str, Any]]:
        trace_id = current_trace_id()
        attributes: dict[str, Any] = {"call_id": call_id, **context_fields()}
        span: TraceSpanRef | None = None
        if trace_id:
            content: dict[str, Any]
            if isinstance(request, ModelRequest):
                content = {
                    "model": request.model,
                    "conversation": [m.model_dump(mode="json") for m in request.messages],
                    "tools": [t.model_dump(mode="json") for t in request.tools],
                    "tool_choice": request.tool_choice.value,
                    "output_schema": request.output_schema_name,
                }
            else:
                content = {
                    "model": request.model,
                    "instruction": request.prompt,
                    "image_mime_type": request.image_mime_type,
                    "image_size_bytes": len(request.image_bytes),
                }
            budget = [80_000]
            attributes["input"] = bounded(content, budget)
            attributes["input_truncated"] = budget[0] < 0
            try:
                async with asyncio.timeout(1):
                    span = await self._traces.start_span(
                        trace_id=trace_id,
                        component="model",
                        operation="model_exchange",
                        attributes=attributes,
                    )
            except Exception:
                logger.warning("model_exchange_start_failed", extra={"call_id": call_id})
        result: dict[str, Any] = {}
        status, error = "completed", None
        try:
            yield result
        except BaseException as exc:
            status, error = "failed", type(exc).__name__
            raise
        finally:
            if span:
                response = result.get("response")
                payload: dict[str, Any] | None
                if isinstance(response, ModelResponse):
                    payload = response.message.model_dump(mode="json")
                elif isinstance(response, VisionInspectionResponse):
                    payload = response.model_dump(
                        mode="json", exclude={"usage", "provider_request_id"}
                    )
                else:
                    payload = None
                budget = [40_000]
                attributes["output"] = bounded(payload, budget)
                attributes["output_truncated"] = budget[0] < 0
                try:
                    async with asyncio.timeout(1):
                        await self._traces.finish_span(
                            span, status=status, attributes=attributes, error_code=error
                        )
                except Exception:
                    logger.warning("model_exchange_finish_failed", extra={"call_id": call_id})
