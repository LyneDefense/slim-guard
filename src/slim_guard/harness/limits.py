from pydantic import BaseModel, ConfigDict, Field


class HarnessLimits(BaseModel):
    """Hard per-turn limits enforced by code rather than model instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int = Field(default=6, ge=1, le=32)
    max_tool_calls: int = Field(default=8, ge=0, le=64)
