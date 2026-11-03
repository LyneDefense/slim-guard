"""Compose request recovery outside per-attempt usage accounting."""

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agent_models.retry import RetryingModelGateway, RetryingVisionGateway
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.observability.ports import ModelExchangeObserver
from slim_guard.usage.gateway import MeasuredModelGateway, MeasuredVisionGateway
from slim_guard.usage.ports import UsageStore


def build_model_gateway(
    gateway: ModelGateway,
    store: UsageStore,
    provider: str = "zhipu",
    *,
    exchanges: ModelExchangeObserver | None = None,
) -> ModelGateway:
    return RetryingModelGateway(MeasuredModelGateway(gateway, store, provider, exchanges))


def build_vision_gateway(
    gateway: VisionModelGateway,
    store: UsageStore,
    provider: str = "zhipu",
    *,
    exchanges: ModelExchangeObserver | None = None,
) -> VisionModelGateway:
    return RetryingVisionGateway(MeasuredVisionGateway(gateway, store, provider, exchanges))
