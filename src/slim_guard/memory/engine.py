from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class MemoryEngineError(RuntimeError):
    """A fail-open semantic projection or recall failure."""


@dataclass(frozen=True, slots=True)
class SemanticMemory:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float | None = None


class MemoryEngine(Protocol):
    provider_name: str

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
    ) -> tuple[SemanticMemory, ...]: ...

    async def upsert_canonical(
        self,
        *,
        user_id: str,
        memory_id: str,
        value_hash: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None: ...

    async def delete_canonical(self, *, user_id: str, memory_id: str) -> None: ...

    async def delete_user(self, *, user_id: str) -> None: ...

    async def close(self) -> None: ...


class NullMemoryEngine:
    provider_name = "disabled"

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
    ) -> tuple[SemanticMemory, ...]:
        del user_id, query, limit
        return ()

    async def upsert_canonical(
        self,
        *,
        user_id: str,
        memory_id: str,
        value_hash: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        del user_id, memory_id, value_hash, text, metadata

    async def delete_canonical(self, *, user_id: str, memory_id: str) -> None:
        del user_id, memory_id

    async def delete_user(self, *, user_id: str) -> None:
        del user_id

    async def close(self) -> None:
        return None


class Mem0HttpMemoryEngine:
    """Small adapter over the self-hosted Mem0 OSS REST API.

    Only canonical SlimGuard facts are projected. Mem0 is deliberately not the
    source of truth and is never exposed directly to the browser.
    """

    provider_name = "mem0"
    _MEMORY_ID_METADATA = "slim_guard_memory_id"
    _VALUE_HASH_METADATA = "slim_guard_value_hash"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        namespace: str = "slim_guard",
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_namespace = namespace.strip()
        if not normalized_namespace:
            raise ValueError("Mem0 namespace cannot be blank")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=headers,
            transport=transport,
        )
        self._namespace = normalized_namespace

    async def close(self) -> None:
        await self._http.aclose()

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
    ) -> tuple[SemanticMemory, ...]:
        if not query.strip():
            return ()
        body = await self._request_json(
            "POST",
            "/search",
            json={
                "query": query,
                "user_id": self._entity_id(user_id),
                "limit": limit,
            },
        )
        return self._memories(body)[:limit]

    async def upsert_canonical(
        self,
        *,
        user_id: str,
        memory_id: str,
        value_hash: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        existing = tuple(
            memory
            for memory in await self._list_user(user_id)
            if memory.metadata.get(self._MEMORY_ID_METADATA) == memory_id
        )
        if any(
            memory.text == text
            and memory.metadata.get(self._VALUE_HASH_METADATA) == value_hash
            for memory in existing
        ):
            return
        for memory in existing:
            await self._request_json("DELETE", f"/memories/{memory.id}")
        await self._request_json(
            "POST",
            "/memories",
            json={
                "messages": [{"role": "user", "content": text}],
                "user_id": self._entity_id(user_id),
                "infer": False,
                "metadata": {
                    **metadata,
                    self._MEMORY_ID_METADATA: memory_id,
                    self._VALUE_HASH_METADATA: value_hash,
                    "source": "slim_guard_canonical",
                },
            },
        )

    async def delete_canonical(self, *, user_id: str, memory_id: str) -> None:
        matches = tuple(
            memory
            for memory in await self._list_user(user_id)
            if memory.metadata.get(self._MEMORY_ID_METADATA) == memory_id
        )
        for memory in matches:
            await self._request_json("DELETE", f"/memories/{memory.id}")

    async def delete_user(self, *, user_id: str) -> None:
        await self._request_json(
            "DELETE",
            "/memories",
            params={"user_id": self._entity_id(user_id)},
        )

    async def _list_user(self, user_id: str) -> tuple[SemanticMemory, ...]:
        body = await self._request_json(
            "GET",
            "/memories",
            params={"user_id": self._entity_id(user_id)},
        )
        return self._memories(body)

    def _entity_id(self, user_id: str) -> str:
        digest = hashlib.sha256(
            f"{self._namespace}:{user_id}".encode()
        ).hexdigest()
        return f"{self._namespace}:{digest}"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(method, path, json=json, params=params)
        except httpx.TimeoutException:
            raise MemoryEngineError("Mem0 request timed out") from None
        except httpx.TransportError:
            raise MemoryEngineError("Mem0 network request failed") from None
        if response.is_error:
            raise MemoryEngineError(f"Mem0 returned HTTP status {response.status_code}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise MemoryEngineError("Mem0 returned a non-JSON response") from None

    @classmethod
    def _memories(cls, body: Any) -> tuple[SemanticMemory, ...]:
        rows: Any = body
        if isinstance(body, dict):
            for key in ("results", "memories"):
                if isinstance(body.get(key), list):
                    rows = body[key]
                    break
        if not isinstance(rows, list):
            return ()
        parsed: list[SemanticMemory] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            memory_id = row.get("id")
            content = row.get("memory", row.get("text"))
            if not isinstance(memory_id, str) or not isinstance(content, str):
                continue
            metadata = row.get("metadata")
            score = row.get("score")
            parsed.append(
                SemanticMemory(
                    id=memory_id,
                    text=content,
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                    score=(
                        float(score)
                        if isinstance(score, (int, float)) and not isinstance(score, bool)
                        else None
                    ),
                )
            )
        return tuple(parsed)
