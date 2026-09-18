from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class NutritionModelGatewayError(RuntimeError):
    """A safe, provider-independent model failure."""


@dataclass(frozen=True, slots=True)
class RerankItem:
    index: int
    score: float


@dataclass(frozen=True, slots=True)
class RerankResult:
    items: tuple[RerankItem, ...]
    model: str
    request_id: str | None
    prompt_tokens: int
    latency_ms: int


class RerankGateway(Protocol):
    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> RerankResult: ...

    async def close(self) -> None: ...


class ZhipuRerankGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "rerank",
        timeout_seconds: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Zhipu API key is required")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> RerankResult:
        query = query.strip()
        normalized = tuple(document.strip() for document in documents)
        if not query or len(query) > 4096:
            raise ValueError("Rerank query must contain 1 to 4096 characters")
        if not normalized or len(normalized) > 128:
            raise ValueError("Rerank requests require 1 to 128 documents")
        if any(not item or len(item) > 4096 for item in normalized):
            raise ValueError("Rerank documents must be nonblank and at most 4096 characters")
        if not 1 <= top_n <= len(normalized):
            raise ValueError("top_n is outside the document collection")
        started = time.monotonic()
        try:
            response = await self._client.post(
                "/rerank",
                json={
                    "model": self._model,
                    "query": query,
                    "documents": list(normalized),
                    "top_n": top_n,
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise NutritionModelGatewayError("rerank_provider_error") from error
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise NutritionModelGatewayError("rerank_response_shape")
        items: list[RerankItem] = []
        seen: set[int] = set()
        for result in results:
            if not isinstance(result, dict):
                raise NutritionModelGatewayError("rerank_response_item")
            index = result.get("index")
            score = result.get("relevance_score")
            if (
                not isinstance(index, int)
                or not 0 <= index < len(normalized)
                or index in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
            ):
                raise NutritionModelGatewayError("rerank_response_value")
            seen.add(index)
            items.append(RerankItem(index=index, score=float(score)))
        items.sort(key=lambda item: (-item.score, item.index))
        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        return RerankResult(
            items=tuple(items[:top_n]),
            model=self._model,
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


__all__ = [
    "NutritionModelGatewayError",
    "RerankGateway",
    "RerankItem",
    "RerankResult",
    "ZhipuRerankGateway",
]
