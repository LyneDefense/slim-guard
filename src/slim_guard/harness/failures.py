from __future__ import annotations

from dataclasses import dataclass

from slim_guard.agent_models.errors import (
    FakeModelScriptExhausted,
    InvalidModelResponse,
    ModelGatewayClosed,
    ModelGatewayError,
    ModelProviderError,
    ModelTimeoutError,
    ModelTransportError,
    UnsupportedModelFeature,
)
from slim_guard.harness.errors import ContextCompilationError


@dataclass(frozen=True, slots=True)
class HarnessFailure:
    code: str
    error_type: str
    retryable: bool
    provider_status_code: int | None = None
    reason_code: str | None = None
    field_path: str | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_payload(self) -> dict[str, str | int | bool | None]:
        return {
            "code": self.code,
            "error_type": self.error_type,
            "retryable": self.retryable,
            "provider_status_code": self.provider_status_code,
            "reason_code": self.reason_code,
            "field_path": self.field_path,
            "finish_reason": self.finish_reason,
            "provider_request_id": self.provider_request_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def model_gateway_failure(error: ModelGatewayError) -> HarnessFailure:
    """Classifies a provider error without persisting its potentially sensitive message."""

    if isinstance(error, ModelTimeoutError):
        return _failure("model_timeout", error, retryable=True)
    if isinstance(error, ModelTransportError):
        return _failure("model_transport_error", error, retryable=True)
    if isinstance(error, ModelProviderError):
        status = error.status_code
        retryable = status in {408, 409, 425, 429} or (status is not None and status >= 500)
        return _failure(
            "model_provider_error",
            error,
            retryable=retryable,
            provider_status_code=status,
        )
    if isinstance(error, InvalidModelResponse):
        return HarnessFailure(
            code="invalid_model_response",
            error_type=type(error).__name__,
            retryable=True,
            reason_code=error.reason_code,
            field_path=error.field_path,
            finish_reason=error.finish_reason,
            provider_request_id=error.provider_request_id,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
        )
    if isinstance(error, UnsupportedModelFeature):
        return _failure("unsupported_model_feature", error, retryable=False)
    if isinstance(error, ModelGatewayClosed):
        return _failure("model_gateway_closed", error, retryable=False)
    if isinstance(error, FakeModelScriptExhausted):
        return _failure("fake_model_script_exhausted", error, retryable=False)
    return _failure("model_gateway_error", error, retryable=False)


def context_compilation_failure(error: ContextCompilationError) -> HarnessFailure:
    return HarnessFailure(
        code="context_compilation_error",
        error_type=type(error).__name__,
        retryable=False,
    )


def _failure(
    code: str,
    error: ModelGatewayError,
    *,
    retryable: bool,
    provider_status_code: int | None = None,
) -> HarnessFailure:
    return HarnessFailure(
        code=code,
        error_type=type(error).__name__,
        retryable=retryable,
        provider_status_code=provider_status_code,
    )
