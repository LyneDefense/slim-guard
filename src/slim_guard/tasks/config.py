from pydantic import BaseModel, ConfigDict, Field


class TaskRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clarification_ttl_days: int = Field(default=14, ge=1, le=90)
