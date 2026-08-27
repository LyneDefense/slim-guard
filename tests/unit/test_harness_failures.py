from slim_guard.agent_models.errors import (
    InvalidModelResponse,
    ModelProviderError,
    ModelTimeoutError,
    UnsupportedModelFeature,
)
from slim_guard.harness.failures import model_gateway_failure


def test_transient_model_failures_are_retryable() -> None:
    timeout = model_gateway_failure(ModelTimeoutError("sensitive timeout detail"))
    overloaded = model_gateway_failure(
        ModelProviderError("sensitive response", status_code=503)
    )
    invalid_response = model_gateway_failure(
        InvalidModelResponse("sensitive malformed body")
    )

    assert timeout.retryable is True
    assert overloaded.retryable is True
    assert overloaded.provider_status_code == 503
    assert invalid_response.retryable is True
    assert "sensitive" not in str(timeout.to_payload())


def test_configuration_and_client_errors_are_not_retryable() -> None:
    bad_request = model_gateway_failure(
        ModelProviderError("sensitive response", status_code=400)
    )
    unsupported = model_gateway_failure(UnsupportedModelFeature("sensitive setting"))

    assert bad_request.retryable is False
    assert bad_request.provider_status_code == 400
    assert unsupported.retryable is False
