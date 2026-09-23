"""Lifetime settings for tool-operation confirmation and approval requests."""

from pydantic import BaseModel, ConfigDict, Field


class PendingActionConfig(BaseModel):
    """Authorization deadlines; not conversation clarification deadlines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_ttl_seconds: int = Field(default=900, ge=1, le=86_400)
    review_ttl_seconds: int = Field(default=86_400, ge=1, le=604_800)
