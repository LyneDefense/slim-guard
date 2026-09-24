"""Retry one model request, never a Turn, Agent invocation or business tool."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

from slim_guard.agent_models.errors import (
    ModelGatewayError,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
)
from slim_guard.agent_models.gateway import ModelGateway, ModelRequest, ModelResponse
from slim_guard.agent_models.vision import (
    VisionInspectionRequest,
    VisionInspectionResponse,
    VisionModelGateway,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Accept HTTP delay-seconds or an HTTP date; ignore malformed headers."""
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            delay = (deadline - (now or datetime.now(UTC))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


@dataclass(frozen=True)
class ModelRetryPolicy:
    # Transport recovery only, not a Token or Agent execution budget.
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Model retry attempts must be positive")
        if (
            not math.isfinite(self.base_delay_seconds)
            or not math.isfinite(self.max_delay_seconds)
            or not 0 < self.base_delay_seconds <= self.max_delay_seconds
        ):
            raise ValueError("Model retry delays must be finite, positive and ordered")


class ModelRequestRetrier:
    def __init__(
        self,
        policy: ModelRetryPolicy | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy or ModelRetryPolicy()
        self._sleep = sleep
        self._jitter = jitter

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await operation()
            except ModelGatewayError as exc:
                delay = self._delay(exc, attempt)
                if delay is None:
                    raise
                # Never log request bodies, provider error bodies or credentials.
                logger.warning(
                    "model_request_retry",
                    extra={
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                        "delay_seconds": delay,
                    },
                )
                # Cancellation and caller timeouts propagate, including while waiting.
                await self._sleep(delay)

    def _delay(self, error: ModelGatewayError, attempt: int) -> float | None:
        if attempt >= self.policy.max_attempts:
            return None
        if isinstance(error, ModelProviderError):
            if error.status_code not in {408, 429, 500, 502, 503, 504}:
                return None
            retry_after = error.retry_after_seconds
            # Do not ignore a long provider backoff by retrying earlier than requested.
            if retry_after is not None and retry_after > self.policy.max_delay_seconds:
                return None
        elif isinstance(error, (ModelTimeoutError, ModelTransportError)):
            retry_after = None
        else:
            return None
        backoff = min(
            self.policy.max_delay_seconds,
            self.policy.base_delay_seconds * 2 ** (attempt - 1),
        )
        return float(
            min(
                self.policy.max_delay_seconds,
                max(retry_after or 0.0, backoff + self._jitter() * backoff * 0.25),
            )
        )


class RetryingModelGateway:
    """Wrap the measured gateway so each attempt is accounted for separately."""

    def __init__(self, gateway: ModelGateway, retrier: ModelRequestRetrier | None = None) -> None:
        self._gateway = gateway
        self._retrier = retrier or ModelRequestRetrier()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._retrier.run(lambda: self._gateway.complete(request))

    async def close(self) -> None:
        await self._gateway.close()


class RetryingVisionGateway:
    def __init__(
        self, gateway: VisionModelGateway, retrier: ModelRequestRetrier | None = None
    ) -> None:
        self._gateway = gateway
        self._retrier = retrier or ModelRequestRetrier()

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        return await self._retrier.run(lambda: self._gateway.inspect(request))

    async def close(self) -> None:
        await self._gateway.close()
