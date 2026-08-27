from __future__ import annotations

import base64
import json

import httpx
import pytest

from slim_guard.agent_models.errors import InvalidModelResponse, ModelProviderError
from slim_guard.agent_models.vision import VisionInspectionRequest
from slim_guard.agent_models.zhipu_vision import ZhipuVisionModelGateway


def request() -> VisionInspectionRequest:
    return VisionInspectionRequest(
        model="glm-5v-turbo",
        prompt="只报告清晰可见的体重。",
        image_bytes=b"\x89PNG\r\n\x1a\nimage",
        image_mime_type="image/png",
        max_output_tokens=512,
        metadata={"user_id": "hashed-user"},
    )


async def test_zhipu_vision_serializes_image_and_normalizes_response() -> None:
    captured: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "vision-response-1",
                "choices": [
                    {"message": {"content": "  体重秤显示 77.6 kg，单位清晰。  "}}
                ],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 10,
                    "total_tokens": 40,
                },
            },
        )

    gateway = ZhipuVisionModelGateway(
        api_key="secret",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await gateway.inspect(request())
    finally:
        await gateway.close()

    encoded = base64.b64encode(request().image_bytes).decode("ascii")
    assert captured["model"] == "glm-5v-turbo"
    assert captured["user_id"] == "hashed-user"
    assert captured["messages"][0]["content"][1] == {  # type: ignore[index]
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }
    assert response.description == "体重秤显示 77.6 kg，单位清晰。"
    assert response.usage.total_tokens == 40
    assert response.provider_request_id == "vision-response-1"


async def test_zhipu_vision_normalizes_provider_and_invalid_response_errors() -> None:
    provider = ZhipuVisionModelGateway(
        api_key="secret",
        base_url="https://example.com",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(429)),
    )
    invalid = ZhipuVisionModelGateway(
        api_key="secret",
        base_url="https://example.com",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    try:
        with pytest.raises(ModelProviderError) as caught:
            await provider.inspect(request())
        assert caught.value.status_code == 429
        with pytest.raises(InvalidModelResponse):
            await invalid.inspect(request())
    finally:
        await provider.close()
        await invalid.close()
