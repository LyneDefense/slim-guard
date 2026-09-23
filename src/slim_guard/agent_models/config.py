"""Model choices and request parameters used by runtime composition."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelRuntimeConfig(BaseModel):
    """Model settings without credentials, environment loading or gateway creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    text_model: str = Field(min_length=1, max_length=256)
    vision_model: str = Field(min_length=1, max_length=256)
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Per-request vision output limit, not a cumulative Agent or Turn budget.
    vision_max_output_tokens: int = Field(default=1024, ge=64, le=32_768)
