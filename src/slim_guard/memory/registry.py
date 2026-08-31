from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from slim_guard.memory.contracts import (
    MemoryCardinality,
    MemoryKey,
    MemoryKind,
    MemorySensitivity,
    PreferenceStance,
    ResponseStyle,
)


def _normalized_text(value: str, *, label: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} cannot contain control characters")
    return normalized


class PreferredNameValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalized_text(value, label="Preferred name")


class ResponseStyleValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    style: ResponseStyle


class FoodPreferenceValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item: str = Field(min_length=1, max_length=128)
    stance: PreferenceStance

    @field_validator("item")
    @classmethod
    def normalize_item(cls, value: str) -> str:
        return _normalized_text(value, label="Food preference item")


class ExercisePreferenceValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    activity: str = Field(min_length=1, max_length=128)
    stance: PreferenceStance

    @field_validator("activity")
    @classmethod
    def normalize_activity(cls, value: str) -> str:
        return _normalized_text(value, label="Exercise preference activity")


class TargetWeightValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grams: int = Field(ge=10_000, le=500_000)
    target_date: date | None = None


class BehaviorGoalValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(pattern=r"^(weekly_exercise_sessions|daily_steps|daily_meal_checkins)$")
    target: int = Field(ge=1, le=200_000)
    period: str = Field(pattern=r"^(day|week)$")

    @model_validator(mode="after")
    def validate_goal_range(self) -> BehaviorGoalValue:
        expected_period = {
            "weekly_exercise_sessions": "week",
            "daily_steps": "day",
            "daily_meal_checkins": "day",
        }[self.kind]
        ranges = {
            "weekly_exercise_sessions": (1, 14),
            "daily_steps": (100, 100_000),
            "daily_meal_checkins": (1, 10),
        }
        lower, upper = ranges[self.kind]
        if self.period != expected_period or not lower <= self.target <= upper:
            raise ValueError("Behavior goal period or target is outside its supported range")
        return self


class ConstraintValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=500)

    @field_validator("subject", "statement")
    @classmethod
    def normalize_constraint_text(cls, value: str) -> str:
        return _normalized_text(value, label="Constraint text")


@dataclass(frozen=True, slots=True)
class MemorySpec:
    key: MemoryKey
    kind: MemoryKind
    cardinality: MemoryCardinality
    sensitivity: MemorySensitivity
    value_model: type[BaseModel]
    entity_field: str | None = None
    review_days: int | None = None


@dataclass(frozen=True, slots=True)
class CanonicalMemory:
    spec: MemorySpec
    value: dict[str, Any]
    value_json: str
    value_hash: str
    slot_key: str


class MemorySchemaRegistry:
    """Versioned allowlist and canonicalizer for durable memory facts."""

    version = "profile-goal-constraint-schema-v2"

    def __init__(self, *, health_review_days: int = 180) -> None:
        if not 30 <= health_review_days <= 730:
            raise ValueError("Health memory review days must be between 30 and 730")
        specs = (
            MemorySpec(
                key=MemoryKey.PREFERRED_NAME,
                kind=MemoryKind.PROFILE,
                cardinality=MemoryCardinality.SINGLE,
                sensitivity=MemorySensitivity.NORMAL,
                value_model=PreferredNameValue,
            ),
            MemorySpec(
                key=MemoryKey.RESPONSE_STYLE,
                kind=MemoryKind.PROFILE,
                cardinality=MemoryCardinality.SINGLE,
                sensitivity=MemorySensitivity.NORMAL,
                value_model=ResponseStyleValue,
            ),
            MemorySpec(
                key=MemoryKey.FOOD_PREFERENCE,
                kind=MemoryKind.PROFILE,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.NORMAL,
                value_model=FoodPreferenceValue,
                entity_field="item",
            ),
            MemorySpec(
                key=MemoryKey.EXERCISE_PREFERENCE,
                kind=MemoryKind.PROFILE,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.NORMAL,
                value_model=ExercisePreferenceValue,
                entity_field="activity",
            ),
            MemorySpec(
                key=MemoryKey.TARGET_WEIGHT,
                kind=MemoryKind.GOAL,
                cardinality=MemoryCardinality.SINGLE,
                sensitivity=MemorySensitivity.HEALTH,
                value_model=TargetWeightValue,
            ),
            MemorySpec(
                key=MemoryKey.BEHAVIOR_GOAL,
                kind=MemoryKind.GOAL,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.NORMAL,
                value_model=BehaviorGoalValue,
                entity_field="kind",
            ),
            MemorySpec(
                key=MemoryKey.DIETARY_CONSTRAINT,
                kind=MemoryKind.CONSTRAINT,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.HEALTH,
                value_model=ConstraintValue,
                entity_field="subject",
                review_days=health_review_days,
            ),
            MemorySpec(
                key=MemoryKey.EXERCISE_CONSTRAINT,
                kind=MemoryKind.CONSTRAINT,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.HEALTH,
                value_model=ConstraintValue,
                entity_field="subject",
                review_days=health_review_days,
            ),
            MemorySpec(
                key=MemoryKey.HEALTH_CONTEXT,
                kind=MemoryKind.CONSTRAINT,
                cardinality=MemoryCardinality.SET,
                sensitivity=MemorySensitivity.RESTRICTED,
                value_model=ConstraintValue,
                entity_field="subject",
                review_days=health_review_days,
            ),
        )
        self._specs = {spec.key: spec for spec in specs}

    @property
    def keys(self) -> tuple[MemoryKey, ...]:
        return tuple(self._specs)

    def canonicalize(self, key: MemoryKey, raw_value: dict[str, Any]) -> CanonicalMemory:
        spec = self._specs[key]
        value = spec.value_model.model_validate(raw_value).model_dump(mode="json")
        value_json = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        value_hash = hashlib.sha256(value_json.encode("utf-8")).hexdigest()
        slot_key = key.value
        if spec.cardinality is MemoryCardinality.SET:
            if spec.entity_field is None:
                raise RuntimeError(f"Set memory is missing its entity field: {key.value}")
            entity = str(value[spec.entity_field]).casefold()
            entity_hash = hashlib.sha256(entity.encode("utf-8")).hexdigest()[:24]
            slot_key = f"{key.value}:{entity_hash}"
        return CanonicalMemory(
            spec=spec,
            value=value,
            value_json=value_json,
            value_hash=value_hash,
            slot_key=slot_key,
        )
