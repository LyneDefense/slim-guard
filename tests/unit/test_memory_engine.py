from __future__ import annotations

import hashlib
import json

import httpx

from slim_guard.memory.engine import Mem0HttpMemoryEngine, MemoryEngineError


async def test_mem0_search_is_always_scoped_to_the_current_user() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "remote-1",
                        "memory": "身高 179cm",
                        "score": 0.91,
                        "metadata": {"slim_guard_memory_id": "memory-1"},
                    }
                ]
            },
        )

    engine = Mem0HttpMemoryEngine(
        base_url="http://mem0.test",
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await engine.search(user_id="user-a", query="我的身高", limit=5)

        assert result[0].metadata["slim_guard_memory_id"] == "memory-1"
        expected_user = "slim_guard:" + hashlib.sha256(
            b"slim_guard:user-a"
        ).hexdigest()
        assert json.loads(requests[0].content) == {
            "query": "我的身高",
            "user_id": expected_user,
            "limit": 5,
        }
        assert requests[0].headers["x-api-key"] == "secret"
    finally:
        await engine.close()


async def test_mem0_canonical_upsert_is_idempotent() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "remote-1",
                        "memory": '身高：{"millimeters": 1790}',
                        "metadata": {
                            "slim_guard_memory_id": "memory-1",
                            "slim_guard_value_hash": "hash-1",
                        },
                    }
                ],
            )
        raise AssertionError("An identical projection must not be written again")

    engine = Mem0HttpMemoryEngine(
        base_url="http://mem0.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await engine.upsert_canonical(
            user_id="user-a",
            memory_id="memory-1",
            value_hash="hash-1",
            text='身高：{"millimeters": 1790}',
            metadata={"memory_key": "profile.height"},
        )
        assert calls == [("GET", "/memories")]
    finally:
        await engine.close()


async def test_mem0_failure_does_not_expose_response_body_or_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text="provider-secret-body")

    engine = Mem0HttpMemoryEngine(
        base_url="http://mem0.test",
        api_key="top-secret-key",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            await engine.search(user_id="user-a", query="身高", limit=5)
        except MemoryEngineError as exc:
            assert str(exc) == "Mem0 returned HTTP status 500"
            assert "secret" not in str(exc)
        else:
            raise AssertionError("Expected a Mem0 error")
    finally:
        await engine.close()
