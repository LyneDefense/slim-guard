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
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agent_models.retry import parse_retry_after
from slim_guard.usage.gateway import capture_provider_usage


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
            try:
                capture_provider_usage(response.json())
            except ValueError:
                pass
            raise ModelProviderError(
                f"Zhipu returned HTTP status {response.status_code}",
                status_code=response.status_code,
                retry_after_seconds=parse_retry_after(response.headers.get("Retry-After")),
            )
        try:
            body = response.json()
        except ValueError:
            raise InvalidModelResponse(
                "Zhipu response was not JSON",
                reason_code="provider_envelope_not_json",
            ) from None
        capture_provider_usage(body)
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
        if request.response_format is ResponseFormat.JSON_OBJECT:
            payload["response_format"] = {"type": "json_object"}
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
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("finish_reason is not text")
            request_id = body.get("id")
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError("response id is not text")
            usage = cls._usage(body.get("usage"))
            if finish_reason == "length":
                raise InvalidModelResponse(
                    "Zhipu output reached the configured token limit",
                    reason_code="output_truncated",
                    repairable=True,
                    output_limit_hit=True,
                    finish_reason=finish_reason,
                    provider_request_id=request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            content = raw_message.get("content")
            if content is not None and not isinstance(content, str):
                raise InvalidModelResponse(
                    "assistant content is not text",
                    reason_code="invalid_assistant_content",
                    field_path="message.content",
                    repairable=True,
                    finish_reason=finish_reason,
                    provider_request_id=request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            tool_calls = cls._tool_calls(raw_message.get("tool_calls"))
            if not tool_calls and not (isinstance(content, str) and content.strip()):
                raise InvalidModelResponse(
                    "assistant response has neither text nor tool calls",
                    reason_code="empty_assistant_response",
                    repairable=True,
                    finish_reason=finish_reason,
                    provider_request_id=request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            return ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=(
                        content.strip() if isinstance(content, str) and content.strip() else None
                    ),
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
                usage=usage,
                provider_request_id=request_id,
            )
        except InvalidModelResponse as exc:
            raise InvalidModelResponse(
                str(exc),
                reason_code=exc.reason_code,
                field_path=exc.field_path,
                repairable=exc.repairable,
                output_limit_hit=exc.output_limit_hit,
                finish_reason=finish_reason,
                provider_request_id=request_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            ) from None
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidModelResponse(
                f"Invalid Zhipu response: {exc}",
                reason_code="invalid_provider_envelope",
            ) from None

    @staticmethod
    def _tool_calls(raw_calls: Any) -> tuple[NormalizedToolCall, ...]:
        if raw_calls is None:
            return ()
        if not isinstance(raw_calls, list):
            raise InvalidModelResponse(
                "tool_calls is not a list",
                reason_code="invalid_tool_calls_shape",
                field_path="message.tool_calls",
                repairable=True,
            )
        calls: list[NormalizedToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            field_path = f"message.tool_calls[{index}]"
            if not isinstance(raw_call, dict):
                raise InvalidModelResponse(
                    "tool call is not an object",
                    reason_code="invalid_tool_call_shape",
                    field_path=field_path,
                    repairable=True,
                )
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise InvalidModelResponse(
                    "tool call does not contain a function",
                    reason_code="invalid_tool_call_shape",
                    field_path=f"{field_path}.function",
                    repairable=True,
                )
            call_id = raw_call.get("id")
            name = function.get("name")
            arguments_text = function.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise InvalidModelResponse(
                    "tool call id or name is not text",
                    reason_code="invalid_tool_call_identity",
                    field_path=field_path,
                    repairable=True,
                )
            if not isinstance(arguments_text, str):
                raise InvalidModelResponse(
                    "tool call arguments are not JSON text",
                    reason_code="invalid_tool_arguments_encoding",
                    field_path=f"{field_path}.function.arguments",
                    repairable=True,
                )
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError:
                raise InvalidModelResponse(
                    "tool call arguments are not valid JSON",
                    reason_code="malformed_tool_arguments_json",
                    field_path=f"{field_path}.function.arguments",
                    repairable=True,
                ) from None
            if not isinstance(arguments, dict):
                raise InvalidModelResponse(
                    "tool call arguments are not an object",
                    reason_code="tool_arguments_not_object",
                    field_path=f"{field_path}.function.arguments",
                    repairable=True,
                )
            try:
                calls.append(NormalizedToolCall(id=call_id, name=name, arguments=arguments))
            except ValidationError:
                raise InvalidModelResponse(
                    "tool call does not match the normalized tool-call contract",
                    reason_code="invalid_tool_call_contract",
                    field_path=field_path,
                    repairable=True,
                ) from None
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
