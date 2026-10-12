"""Validated configuration for application-level Agent assembly."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.tasks.config import TaskRuntimeConfig


class AgentRuntimeDefinition(BaseModel):
    """Capability-owned runtime configuration for the single active architecture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    models: ModelRuntimeConfig
    code_revision: str = Field(min_length=1, max_length=256)
    tasks: TaskRuntimeConfig = Field(default_factory=TaskRuntimeConfig)
    memory: MemoryRuntimeConfig = Field(default_factory=MemoryRuntimeConfig)
    agent_specialist_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
