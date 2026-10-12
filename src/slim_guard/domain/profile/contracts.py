from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class CoachProfileSnapshot:
    schema_version: int
    revision: int
    age_band: str
    height_millimeters: int
    current_weight_grams: int
    weight_measured_on: date
    goal_type: str
    target_weight_grams: int
    target_date: date
    current_body_fat_basis_points: int | None
    target_body_fat_basis_points: int | None
    exercise_frequency: str | None
    completed_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UserProfileSnapshot:
    nickname: str | None
    first_seen_at: datetime
    coach_profile: CoachProfileSnapshot | None = None
