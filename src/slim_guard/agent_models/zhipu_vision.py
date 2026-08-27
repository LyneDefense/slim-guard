from __future__ import annotations

import base64
from typing import Any

import httpx

from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
)
from slim_guard.agent_models.gateway import ModelUsage
from slim_guard.agent_models.vision import (
    VisionInspectionRequest,
    VisionInspectionResponse,
)


class ZhipuVisionModelGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
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

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        encoded = base64.b64encode(request.image_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{request.image_mime_type};base64,{encoded}"
                                )
                            },
                        },
                    ],
                }
            ],
            "thinking": {"type": "disabled"},
            "do_sample": False,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if user_id := request.metadata.get("user_id"):
            payload["user_id"] = user_id
        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TimeoutException:
            raise ModelTimeoutError("Zhipu vision request timed out") from None
        except httpx.TransportError:
            raise ModelTransportError("Zhipu vision network request failed") from None
        if response.is_error:
            raise ModelProviderError(
                f"Zhipu vision returned HTTP status {response.status_code}",
                status_code=response.status_code,
            )
        try:
            body = response.json()
            return self._parse_response(body)
        except (TypeError, ValueError, KeyError):
            raise InvalidModelResponse("Zhipu vision response is invalid") from None

    @staticmethod
    def _parse_response(body: Any) -> VisionInspectionResponse:
        if not isinstance(body, dict):
            raise ValueError("response body is not an object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("response does not contain choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("response choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise ValueError("response choice does not contain a message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response does not contain description text")
        request_id = body.get("id")
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError("response id is not text")
        usage = body.get("usage")
        return VisionInspectionResponse(
            description=content.strip(),
            usage=ZhipuVisionModelGateway._usage(usage),
            provider_request_id=request_id,
        )

    @staticmethod
    def _usage(raw: Any) -> ModelUsage:
        if raw is None:
            return ModelUsage()
        if not isinstance(raw, dict):
            raise ValueError("usage is not an object")
        return ModelUsage(
            input_tokens=ZhipuVisionModelGateway._token_count(
                raw.get("prompt_tokens", raw.get("input_tokens", 0))
            ),
            output_tokens=ZhipuVisionModelGateway._token_count(
                raw.get("completion_tokens", raw.get("output_tokens", 0))
            ),
            total_tokens=ZhipuVisionModelGateway._token_count(
                raw.get("total_tokens", 0)
            ),
        )

    @staticmethod
    def _token_count(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("token count is invalid")
        return value
