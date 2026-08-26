from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

SLIM_GUARD_INSTRUCTIONS = "\n".join(
    (
        "你是 SlimGuard，一位温和、专业、务实的减脂记录助手。",
        "你现在只能看到用户本次发来的文字或图片，没有历史记录，不要声称你记得之前的内容。",
        "",
        "你的任务：",
        "1. 用简体中文回复，先准确理解用户这次的体重、饮食、运动或减脂问题，"
        "再给出简短、可执行的反馈。",
        "2. 如果是体重秤照片，提取清晰可见的数值和单位；如果是食物照片，"
        "识别主要食物，仅对份量和营养结构做合理估计；如果是运动截图，"
        "提取清晰可见的项目和数据。",
        "3. 图片模糊、数值遮挡或无法确定时，明确说不确定并请用户确认，绝不编造数据。",
        "4. 不诊断疾病，不开药，不推荐极端节食、催吐、泻药或危险减重方式。"
        "遇到昏厥、胸痛、严重低血糖、持续呕吐等危险信号，建议立即就医。",
        "5. 不要把单次体重波动解读为脂肪增减，强调趋势和可持续性。",
        "6. 回复尽量控制在 300 个中文字内，不用 Markdown 表格，不写长篇说教。"
        "信息不足时最多问一个关键问题。",
    )
)


@dataclass(frozen=True, slots=True)
class ReplyRequest:
    user_id: str
    nickname: str | None
    text: str | None = None
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


class ReplyAgentProtocol(Protocol):
    async def generate_reply(self, request: ReplyRequest) -> str: ...


class StaticReplyAgent:
    """Safe startup fallback used when OPENAI_KEY is absent."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def generate_reply(self, request: ReplyRequest) -> str:
        return self.reply


class OpenAIReplyError(RuntimeError):
    pass


class OpenAIReplyAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_output_tokens: int,
        max_reply_chars: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_reply_chars = max_reply_chars
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def generate_reply(self, request: ReplyRequest) -> str:
        content: list[dict[str, str]] = []
        context = f"客户昵称：{request.nickname}" if request.nickname else "客户昵称：未知"
        if request.text:
            context = f"{context}\n用户本次消息：{request.text}"
        elif request.image_bytes is not None:
            context = f"{context}\n用户本次发来一张图片，请识别并按照你的任务回复。"
        content.append({"type": "input_text", "text": context})
        if request.image_bytes is not None:
            mime_type = _supported_image_mime_type(
                request.image_bytes,
                request.image_mime_type,
            )
            encoded = base64.b64encode(request.image_bytes).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "high",
                }
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": SLIM_GUARD_INSTRUCTIONS,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "safety_identifier": hashlib.sha256(request.user_id.encode()).hexdigest(),
        }
        try:
            response = await self._http.post("/responses", json=payload)
        except httpx.TimeoutException:
            raise OpenAIReplyError("OpenAI request timed out") from None
        except httpx.TransportError:
            raise OpenAIReplyError("OpenAI network request failed") from None
        if response.is_error:
            raise OpenAIReplyError(f"OpenAI returned HTTP status {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise OpenAIReplyError("OpenAI response was not JSON") from None
        output_text = _extract_output_text(body)
        if not output_text:
            raise OpenAIReplyError("OpenAI response did not contain output text")
        return output_text[: self.max_reply_chars]


def _extract_output_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    texts: list[str] = []
    output = body.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for part in item["content"]:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                texts.append(part["text"])
    return "\n".join(texts).strip()


def _supported_image_mime_type(image: bytes, declared: str | None) -> str:
    normalized = (declared or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        return normalized
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(image) >= 12 and image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    raise OpenAIReplyError("Unsupported image format")
