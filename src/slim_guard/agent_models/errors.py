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
    pass


class UnsupportedModelFeature(ModelGatewayError):
    pass


class ModelGatewayClosed(ModelGatewayError):
    pass


class FakeModelScriptExhausted(ModelGatewayError):
    pass
