from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class EmbeddingError(RuntimeError):
    """Safe, provider-independent embedding failure."""


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    request_id: str | None
    prompt_tokens: int
    latency_ms: int


class EmbeddingGateway(Protocol):
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...

    async def close(self) -> None: ...


class ZhipuEmbeddingGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "embedding-3",
        dimensions: int = 1024,
        timeout_seconds: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Zhipu API key is required")
        if dimensions not in {256, 512, 1024, 2048}:
            raise ValueError("Unsupported embedding dimensions")
        self._model = model
        self._dimensions = dimensions
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        normalized = tuple(text.strip() for text in texts)
        if not normalized or len(normalized) > 64:
            raise ValueError("Embedding requests require 1 to 64 texts")
        if any(not text or len(text) > 12_000 for text in normalized):
            raise ValueError("Embedding texts must be nonblank and bounded")
        started = time.monotonic()
        try:
            response = await self._client.post(
                "/embeddings",
                json={
                    "model": self._model,
                    "input": list(normalized),
                    "dimensions": self._dimensions,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmbeddingError("embedding_provider_error") from error
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(normalized):
            raise EmbeddingError("embedding_response_shape")
        ordered: list[tuple[float, ...] | None] = [None] * len(normalized)
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                raise EmbeddingError("embedding_response_item")
            index = item["index"]
            vector = item.get("embedding")
            if not 0 <= index < len(ordered) or ordered[index] is not None:
                raise EmbeddingError("embedding_response_index")
            if (
                not isinstance(vector, list)
                or len(vector) != self._dimensions
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    for value in vector
                )
            ):
                raise EmbeddingError("embedding_response_vector")
            ordered[index] = tuple(float(value) for value in vector)
        if any(vector is None for vector in ordered):
            raise EmbeddingError("embedding_response_missing")
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        return EmbeddingBatch(
            vectors=tuple(vector for vector in ordered if vector is not None),
            model=str(payload.get("model") or self._model),
            request_id=_request_id(payload, response),
            prompt_tokens=_safe_int(usage.get("prompt_tokens")),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _request_id(payload: Any, response: httpx.Response) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("request_id") or payload.get("id")
        if isinstance(value, str) and value:
            return value[:256]
    value = response.headers.get("x-request-id")
    return value[:256] if value else None
