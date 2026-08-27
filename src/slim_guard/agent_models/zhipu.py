from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
    UnsupportedModelFeature,
)
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NormalizedToolCall,
    ToolChoice,
)


class ZhipuModelGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        thinking_enabled: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.thinking_enabled = thinking_enabled
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

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload = self._request_payload(request)
        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TimeoutException:
            raise ModelTimeoutError("Zhipu request timed out") from None
        except httpx.TransportError:
            raise ModelTransportError("Zhipu network request failed") from None
        if response.is_error:
            raise ModelProviderError(
                f"Zhipu returned HTTP status {response.status_code}",
                status_code=response.status_code,
            )
        try:
            body = response.json()
        except ValueError:
            raise InvalidModelResponse("Zhipu response was not JSON") from None
        return self._model_response(body)

    def _request_payload(self, request: ModelRequest) -> dict[str, Any]:
        if request.tool_choice is ToolChoice.REQUIRED:
            raise UnsupportedModelFeature("Zhipu does not support tool_choice=required")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self._message_payload(message) for message in request.messages],
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
            "do_sample": request.temperature is not None,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tool_choice is ToolChoice.AUTO and request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_json_schema,
                    },
                }
                for tool in request.tools
            ]
            payload["tool_choice"] = ToolChoice.AUTO.value
        if user_id := request.metadata.get("user_id"):
            payload["user_id"] = user_id
        return payload

    @staticmethod
    def _message_payload(message: ModelMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role is MessageRole.TOOL:
            payload["tool_call_id"] = message.tool_call_id
        return payload

    @classmethod
    def _model_response(cls, body: Any) -> ModelResponse:
        try:
            if not isinstance(body, dict):
                raise ValueError("response body is not an object")
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("response does not contain choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError("response choice is not an object")
            raw_message = choice.get("message")
            if not isinstance(raw_message, dict):
                raise ValueError("response choice does not contain a message")
            content = raw_message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("assistant content is not text")
            tool_calls = cls._tool_calls(raw_message.get("tool_calls"))
            usage = cls._usage(body.get("usage"))
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("finish_reason is not text")
            request_id = body.get("id")
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError("response id is not text")
            return ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=(
                        content.strip()
                        if isinstance(content, str) and content.strip()
                        else None
                    ),
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
                usage=usage,
                provider_request_id=request_id,
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidModelResponse(f"Invalid Zhipu response: {exc}") from None

    @staticmethod
    def _tool_calls(raw_calls: Any) -> tuple[NormalizedToolCall, ...]:
        if raw_calls is None:
            return ()
        if not isinstance(raw_calls, list):
            raise ValueError("tool_calls is not a list")
        calls: list[NormalizedToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise ValueError("tool call is not an object")
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise ValueError("tool call does not contain a function")
            call_id = raw_call.get("id")
            name = function.get("name")
            arguments_text = function.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise ValueError("tool call id or name is not text")
            if not isinstance(arguments_text, str):
                raise ValueError("tool call arguments are not JSON text")
            arguments = json.loads(arguments_text)
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments are not an object")
            calls.append(NormalizedToolCall(id=call_id, name=name, arguments=arguments))
        return tuple(calls)

    @staticmethod
    def _usage(raw_usage: Any) -> ModelUsage:
        if raw_usage is None:
            return ModelUsage()
        if not isinstance(raw_usage, dict):
            raise ValueError("usage is not an object")
        return ModelUsage(
            input_tokens=ZhipuModelGateway._token_count(
                raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)),
                "input tokens",
            ),
            output_tokens=ZhipuModelGateway._token_count(
                raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)),
                "output tokens",
            ),
            total_tokens=ZhipuModelGateway._token_count(
                raw_usage.get("total_tokens", 0),
                "total tokens",
            ),
        )

    @staticmethod
    def _token_count(value: Any, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{label} is not a non-negative integer")
        return value
