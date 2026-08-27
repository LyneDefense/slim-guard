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


@dataclass(frozen=True, slots=True)
class HarnessFailure:
    code: str
    error_type: str
    retryable: bool
    provider_status_code: int | None = None

    def to_payload(self) -> dict[str, str | int | bool | None]:
        return {
            "code": self.code,
            "error_type": self.error_type,
            "retryable": self.retryable,
            "provider_status_code": self.provider_status_code,
        }


def model_gateway_failure(error: ModelGatewayError) -> HarnessFailure:
    """Classifies a provider error without persisting its potentially sensitive message."""

    if isinstance(error, ModelTimeoutError):
        return _failure("model_timeout", error, retryable=True)
    if isinstance(error, ModelTransportError):
        return _failure("model_transport_error", error, retryable=True)
    if isinstance(error, ModelProviderError):
        status = error.status_code
        retryable = status in {408, 409, 425, 429} or (
            status is not None and status >= 500
        )
        return _failure(
            "model_provider_error",
            error,
            retryable=retryable,
            provider_status_code=status,
        )
    if isinstance(error, InvalidModelResponse):
        return _failure("invalid_model_response", error, retryable=True)
    if isinstance(error, UnsupportedModelFeature):
        return _failure("unsupported_model_feature", error, retryable=False)
    if isinstance(error, ModelGatewayClosed):
        return _failure("model_gateway_closed", error, retryable=False)
    if isinstance(error, FakeModelScriptExhausted):
        return _failure("fake_model_script_exhausted", error, retryable=False)
    return _failure("model_gateway_error", error, retryable=False)


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
