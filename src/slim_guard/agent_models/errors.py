from __future__ import annotations


class ModelGatewayError(RuntimeError):
    """Base class for normalized model-provider failures."""


class ModelTimeoutError(ModelGatewayError):
    pass


class ModelTransportError(ModelGatewayError):
    pass


class ModelProviderError(ModelGatewayError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class InvalidModelResponse(ModelGatewayError):
    pass


class UnsupportedModelFeature(ModelGatewayError):
    pass
