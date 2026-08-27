from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.agent_models.gateway import ModelUsage


class VisionInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=12_000)
    image_bytes: bytes = Field(min_length=1, max_length=20_971_520)
    image_mime_type: str = Field(
        pattern=r"^image/(jpeg|png|gif|webp)$",
        max_length=32,
    )
    max_output_tokens: int = Field(default=1024, ge=1, le=32_768)
    metadata: dict[str, str] = Field(default_factory=dict)


class VisionInspectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1, max_length=20_000)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    provider_request_id: str | None = None


class VisionModelGateway(Protocol):
    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse: ...

    async def close(self) -> None: ...
