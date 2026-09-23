"""Assemble chat attachment storage and general image understanding together."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.db.session import Database
from slim_guard.domain.assets import ImageAssetRepository
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.image import image_tool_executors


@dataclass(frozen=True, slots=True)
class AttachmentComponents:
    assets: ImageAssetRepository
    executors: Mapping[str, ToolExecutor[Any]]


def build_attachment_components(
    *,
    database: Database,
    models: ModelRuntimeConfig,
    vision: VisionModelGateway | None,
    clock: Callable[[], datetime] | None,
) -> AttachmentComponents:
    assets = ImageAssetRepository(database)
    return AttachmentComponents(
        assets=assets,
        executors=image_tool_executors(
            assets=assets,
            vision=vision,
            vision_model=models.vision_model,
            max_output_tokens=models.vision_max_output_tokens,
            clock=clock,
        ),
    )
