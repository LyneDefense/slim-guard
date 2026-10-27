from __future__ import annotations


class ModelGatewayError(RuntimeError):
    """Base class for normalized model-provider failures."""


class ModelTimeoutError(ModelGatewayError):
    pass


class ModelTransportError(ModelGatewayError):
    pass


class ModelProviderError(ModelGatewayError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class InvalidModelResponse(ModelGatewayError):
    """A provider response the gateway could not normalize.

    Only sanitized diagnostics are retained. In particular, raw provider output is
    deliberately excluded because it can contain user data or hidden prompt text.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "invalid_provider_response",
        field_path: str | None = None,
        repairable: bool = False,
        output_limit_hit: bool = False,
        finish_reason: str | None = None,
        provider_request_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.field_path = field_path
        self.repairable = repairable
        self.output_limit_hit = output_limit_hit
        self.finish_reason = finish_reason
        self.provider_request_id = provider_request_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class UnsupportedModelFeature(ModelGatewayError):
    pass


class ModelGatewayClosed(ModelGatewayError):
    pass


class FakeModelScriptExhausted(ModelGatewayError):
    pass
