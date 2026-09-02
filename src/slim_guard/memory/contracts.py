from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryKind(StrEnum):
    PROFILE = "profile"
    GOAL = "goal"
    CONSTRAINT = "constraint"


class MemoryKey(StrEnum):
    PREFERRED_NAME = "identity.preferred_name"
    HEIGHT = "profile.height"
    EXERCISE_HABIT = "profile.exercise_habit"
    RESPONSE_STYLE = "coaching.response_style"
    FOOD_PREFERENCE = "food.preference"
    EXERCISE_PREFERENCE = "exercise.preference"
    TARGET_WEIGHT = "goal.target_weight"
    TARGET_BODY_FAT = "goal.target_body_fat"
    BEHAVIOR_GOAL = "goal.behavior"
    DIETARY_CONSTRAINT = "constraint.dietary"
    EXERCISE_CONSTRAINT = "constraint.exercise"
    HEALTH_CONTEXT = "constraint.health_context"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    EXPIRED = "expired"


class MemoryAssertion(StrEnum):
    USER_EXPLICIT = "user_explicit"
    USER_CONFIRMED = "user_confirmed"
    IMPORTED = "imported"


class MemorySensitivity(StrEnum):
    NORMAL = "normal"
    HEALTH = "health"
    RESTRICTED = "restricted"


class MemoryCardinality(StrEnum):
    SINGLE = "single"
    SET = "set"


class PreferenceStance(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"
    AVOID = "avoid"


class ResponseStyle(StrEnum):
    CONCISE = "concise"
    DETAILED = "detailed"
    GENTLE = "gentle"
    DIRECT = "direct"


class MemoryFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: MemoryKey
    value: dict[str, Any]


class MemoryWriteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    facts: tuple[MemoryFactInput, ...] = Field(min_length=1, max_length=8)
    evidence_excerpt: str = Field(min_length=1, max_length=512)
    operation_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str = Field(min_length=1, max_length=128)
    evidence_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)
    assertion: MemoryAssertion = MemoryAssertion.USER_EXPLICIT

    @field_validator("evidence_excerpt")
    @classmethod
    def normalize_evidence(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Memory evidence cannot be blank")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_keys(self) -> MemoryWriteCommand:
        keys = tuple(fact.key for fact in self.facts)
        if len(keys) != len(set(keys)):
            raise ValueError("A memory write cannot repeat the same key")
        return self


class MemoryRevokeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    memory_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str = Field(min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)


class MemoryBulkRevokeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^profile_goal_constraint$")
    evidence_excerpt: str = Field(min_length=1, max_length=512)
    operation_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_item_id: str = Field(min_length=1, max_length=128)
    source_tool_call_id: str = Field(min_length=1, max_length=256)

    @field_validator("evidence_excerpt")
    @classmethod
    def normalize_bulk_evidence(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Bulk memory evidence cannot be blank")
        return normalized


class MemoryFactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    kind: MemoryKind
    key: MemoryKey
    slot_key: str
    value: dict[str, Any]
    value_hash: str
    status: MemoryStatus
    assertion: MemoryAssertion
    sensitivity: MemorySensitivity
    supersedes_id: str | None
    source_turn_id: str
    source_item_id: str
    evidence_item_id: str
    source_tool_call_id: str
    valid_from: datetime
    review_after: datetime | None
    created_at: datetime
    ended_at: datetime | None


class MemoryWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    facts: tuple[MemoryFactRef, ...]
    created_count: int = Field(ge=0)


class MemoryRevokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact: MemoryFactRef
    changed: bool


class MemoryBulkRevokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str
    memory_ids: tuple[str, ...]
    revoked_count: int = Field(ge=0)
