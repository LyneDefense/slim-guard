"""Memory configuration shared by runtime composition and memory components."""

from pydantic import BaseModel, ConfigDict, Field


class MemoryRuntimeConfig(BaseModel):
    """Validated memory settings; environment loading belongs to application startup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Structured profile facts preloaded into context and their review interval.
    preload_max_facts: int = Field(default=30, ge=1, le=100)
    health_review_days: int = Field(default=180, ge=30, le=730)

    # Working context limits. These do not delete stored conversation history.
    recent_turn_count: int = Field(default=3, ge=1, le=10)
    recent_dialogue_max_chars: int = Field(default=1500, ge=100, le=10_000)
    # Remove with the independent image window when attachments join message context.

    # Unfinished-summary lifetime; revisit when the shared task lifecycle is integrated.

    # Long-term recall candidate and final selection limits.
    recall_search_limit: int = Field(default=12, ge=1, le=100)
    recall_max_selected: int = Field(default=8, ge=1, le=20)
