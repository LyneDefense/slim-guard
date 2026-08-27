from pydantic import BaseModel, ConfigDict, Field


class HarnessLimits(BaseModel):
    """Hard per-turn limits enforced by code rather than model instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int = Field(default=6, ge=1, le=32)
    max_tool_calls: int = Field(default=8, ge=0, le=64)
    max_total_tokens: int = Field(default=64_000, ge=1, le=10_000_000)
