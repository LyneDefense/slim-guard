from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from slim_guard.services.reply_agent import ReplyRequest, ZhipuReplyAgent, ZhipuReplyError


async def test_zhipu_agent_builds_single_turn_text_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  今天的饮食记录收到了。  "}}]},
        )

    agent = ZhipuReplyAgent(
        api_key="secret-key",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        max_output_tokens=500,
        max_reply_chars=1500,
        transport=httpx.MockTransport(handler),
    )
    try:
        reply = await agent.generate_reply(
            ReplyRequest(
                user_id="internal-user-1",
                nickname="小明",
                text="中午吃了鸡胸肉和米饭",
            )
        )
    finally:
        await agent.close()

    assert reply == "今天的饮食记录收到了。"
    body = json.loads(requests[0].content)
    assert body["model"] == "glm-5.2"
    assert body["thinking"] == {"type": "disabled"}
    assert body["do_sample"] is False
    assert body["user_id"] == hashlib.sha256(b"internal-user-1").hexdigest()
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == [
        {
            "type": "text",
            "text": "客户昵称：小明\n用户本次消息：中午吃了鸡胸肉和米饭",
        }
    ]
    assert requests[0].headers["authorization"] == "Bearer secret-key"
    assert requests[0].url.path == "/api/paas/v4/chat/completions"


async def test_zhipu_agent_routes_image_to_vision_model() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "体重秤显示 77.8 kg。"}}]},
        )

    image = b"\x89PNG\r\n\x1a\nimage"
    agent = ZhipuReplyAgent(
        api_key="secret-key",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        max_output_tokens=500,
        max_reply_chars=1500,
        transport=httpx.MockTransport(handler),
    )
    try:
        reply = await agent.generate_reply(
            ReplyRequest(user_id="user-1", nickname=None, image_bytes=image)
        )
    finally:
        await agent.close()

    assert reply == "体重秤显示 77.8 kg。"
    assert captured["model"] == "glm-5v-turbo"
    image_part = captured["messages"][1]["content"][1]  # type: ignore[index]
    assert image_part == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"},
    }


async def test_zhipu_agent_rejects_unsupported_image_before_request() -> None:
    agent = ZhipuReplyAgent(
        api_key="secret-key",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout_seconds=1,
        max_output_tokens=500,
        max_reply_chars=1500,
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    try:
        with pytest.raises(ZhipuReplyError, match="Unsupported image format"):
            await agent.generate_reply(
                ReplyRequest(user_id="user-1", nickname=None, image_bytes=b"not-an-image")
            )
    finally:
        await agent.close()
