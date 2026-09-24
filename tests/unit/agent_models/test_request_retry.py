import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from slim_guard.agent_models.assembly import build_model_gateway, build_vision_gateway
from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
    UnsupportedModelFeature,
)
from slim_guard.agent_models.gateway import ModelMessage, ModelRequest
from slim_guard.agent_models.retry import ModelRequestRetrier, ModelRetryPolicy, parse_retry_after
from slim_guard.agent_models.vision import VisionInspectionRequest
from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.agent_models.zhipu_vision import ZhipuVisionModelGateway
from slim_guard.usage.context import CURRENT, usage_scope


class Store:
    def __init__(self):
        self.rows = {}

    async def start(self, values):
        self.rows[values["id"]] = dict(values)

    async def finish(self, key, values):
        self.rows[key].update(values)


@pytest.fixture
def delays(monkeypatch):
    values = []

    async def sleep(delay):
        values.append(delay)
        await asyncio.sleep(0)

    # Exercise production assembly with a deterministic clock-free retrier.
    original = ModelRequestRetrier.__init__

    def initialize(self, policy=None):
        original(self, policy, sleep=sleep, jitter=lambda: 0)

    monkeypatch.setattr(ModelRequestRetrier, "__init__", initialize)
    return values


def request():
    return ModelRequest(
        purpose="harness_turn", model="test", messages=(ModelMessage(role="user", content="hi"),)
    )


@pytest.mark.parametrize("vision", [False, True])
async def test_http_attempts_have_individual_usage_and_unchanged_payload(vision, delays):
    payloads = []
    store = Store()

    def handler(req):
        payloads.append(json.loads(req.content))
        if len(payloads) == 1:
            raise httpx.ReadTimeout("sensitive transport detail", request=req)
        if len(payloads) == 2:
            return httpx.Response(
                429,
                headers={"Retry-After": "4"},
                json={"id": "failed-attempt", "usage": {"total_tokens": 3}},
            )
        content = (
            json.dumps(
                {
                    "category": "other",
                    "summary": "一张图片",
                    "observations": [],
                    "requires_user_confirmation": False,
                }
            )
            if vision
            else "ok"
        )
        return httpx.Response(
            200,
            json={
                "id": "success-attempt",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            },
        )

    raw = (ZhipuVisionModelGateway if vision else ZhipuModelGateway)(
        api_key="secret",
        base_url="https://provider.test",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    gateway = (build_vision_gateway if vision else build_model_gateway)(raw, store)
    try:
        with usage_scope(user_id="user", turn_id="turn", invocation_id="inv", agent_role="core"):
            if vision:
                await gateway.inspect(
                    VisionInspectionRequest(
                        model="vision",
                        prompt="describe",
                        image_bytes=b"image",
                        image_mime_type="image/jpeg",
                    )
                )
            else:
                assert (await gateway.complete(request())).message.content == "ok"
    finally:
        await gateway.close()
    assert len(payloads) == 3 and payloads[0] == payloads[1] == payloads[2]
    assert delays == [1, 4]
    rows = list(store.rows.values())
    assert [r["status"] for r in rows] == ["failed", "failed", "succeeded"]
    assert [r.get("total_tokens") for r in rows] == [None, 3, 10]
    assert rows[1]["provider_request_id"] == "failed-attempt"
    assert rows[2]["provider_request_id"] == "success-attempt"
    assert all(r["user_id"] == "user" and r["invocation_id"] == "inv" for r in rows)
    assert CURRENT.get().user_id is None


@pytest.mark.parametrize(
    "error",
    [
        ModelTimeoutError("timeout"),
        ModelTransportError("network"),
        *(ModelProviderError("temporary", status_code=c) for c in (408, 429, 500, 502, 503, 504)),
    ],
)
async def test_transient_failure_exhaustion_preserves_original_error(error, delays):
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(type(error)) as caught:
        await ModelRequestRetrier().run(fail)
    assert caught.value is error
    assert calls == 3 and delays == [1, 2]


@pytest.mark.parametrize(
    "error",
    [
        *(ModelProviderError("rejected", status_code=c) for c in (400, 401, 403, 404, 422, 501)),
        InvalidModelResponse("bad JSON"),
        UnsupportedModelFeature("unsupported"),
        ValueError("programming error"),
        ModelProviderError("slow down", status_code=429, retry_after_seconds=60),
    ],
)
async def test_permanent_or_nontransport_failures_are_not_retried(error, delays):
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(type(error)) as caught:
        await ModelRequestRetrier().run(fail)
    assert caught.value is error
    assert calls == 1 and delays == []


@pytest.mark.parametrize("phase", ["request", "backoff"])
async def test_cancellation_stops_attempts_and_records_only_started_requests(phase):
    entered = asyncio.Event()
    calls = 0
    store = Store()

    async def handler(req):
        nonlocal calls
        calls += 1
        if phase == "backoff":
            return httpx.Response(429, headers={"Retry-After": "30"})
        entered.set()
        await asyncio.Event().wait()

    raw = ZhipuModelGateway(
        api_key="secret",
        base_url="https://provider.test",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    gateway = build_model_gateway(raw, store)
    original_finish = store.finish

    async def finish(key, values):
        await original_finish(key, values)
        if phase == "backoff":
            entered.set()

    store.finish = finish
    task = asyncio.create_task(gateway.complete(request()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await gateway.close()
    assert calls == 1
    (row,) = store.rows.values()
    assert row["status"] == ("cancelled" if phase == "request" else "failed")
    assert row.get("total_tokens") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("broken", None),
        ("NaN", None),
        ("inf", None),
        ("3", 3),
        ("0", 0),
        ("Wed, 23 Sep 2026 10:00:07 GMT", 7),
        ("Wed, 23 Sep 2026 09:59:00 GMT", 0),
    ],
)
def test_retry_after_header(value, expected):
    assert parse_retry_after(value, now=datetime(2026, 9, 23, 10, tzinfo=UTC)) == expected


def test_invalid_retry_policy():
    for kwargs in (
        {"max_attempts": 0},
        {"base_delay_seconds": -1},
        {"max_delay_seconds": float("inf")},
    ):
        with pytest.raises(ValueError):
            ModelRetryPolicy(**kwargs)


async def test_invalid_success_response_is_not_network_retried_but_usage_is_saved(delays):
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": [], "usage": {"total_tokens": 9}})

    store = Store()
    raw = ZhipuModelGateway(
        api_key="secret",
        base_url="https://provider.test",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    gateway = build_model_gateway(raw, store)
    try:
        with pytest.raises(InvalidModelResponse):
            await gateway.complete(request())
    finally:
        await gateway.close()
    assert calls == 1 and delays == []
    (row,) = store.rows.values()
    assert row["status"] == "failed" and row["total_tokens"] == 9


async def test_provider_backoff_is_not_shortened(delays):
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise ModelProviderError("rate limited", status_code=429, retry_after_seconds=30)

    with pytest.raises(ModelProviderError):
        await ModelRequestRetrier().run(fail)
    assert calls == 3 and delays == [30, 30]
