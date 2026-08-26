from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from slim_guard.services.reply_agent import OpenAIReplyAgent, OpenAIReplyError, ReplyRequest


async def test_openai_agent_builds_stateless_text_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "  今天的饮食记录收到了。  "}],
                    }
                ]
            },
        )

    agent = OpenAIReplyAgent(
        api_key="secret-key",
        model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
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
    assert body["model"] == "gpt-4.1-mini"
    assert body["store"] is False
    assert body["safety_identifier"] == hashlib.sha256(b"internal-user-1").hexdigest()
    assert body["input"][0]["content"] == [
        {
            "type": "input_text",
            "text": "客户昵称：小明\n用户本次消息：中午吃了鸡胸肉和米饭",
        }
    ]
    assert requests[0].headers["authorization"] == "Bearer secret-key"


async def test_openai_agent_sends_image_as_data_url() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": "体重秤显示 77.8 kg。"})

    image = b"\x89PNG\r\n\x1a\nimage"
    agent = OpenAIReplyAgent(
        api_key="secret-key",
        model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
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
    image_part = captured["input"][0]["content"][1]  # type: ignore[index]
    assert image_part == {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}",
        "detail": "high",
    }


async def test_openai_agent_rejects_unsupported_image_before_request() -> None:
    agent = OpenAIReplyAgent(
        api_key="secret-key",
        model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        timeout_seconds=1,
        max_output_tokens=500,
        max_reply_chars=1500,
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    try:
        with pytest.raises(OpenAIReplyError, match="Unsupported image format"):
            await agent.generate_reply(
                ReplyRequest(user_id="user-1", nickname=None, image_bytes=b"not-an-image")
            )
    finally:
        await agent.close()
