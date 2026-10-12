from __future__ import annotations

from sqlalchemy import select

from slim_guard.db.models import MobileCoachProfileRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.profile.contracts import CoachProfileSnapshot, UserProfileSnapshot


class UserProfileRepository:
    """Read user and onboarding profile data behind a domain-facing contract."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, user_id: str) -> UserProfileSnapshot | None:
        async with self._database.session() as session:
            user = await session.scalar(select(SlimGuardUser).where(SlimGuardUser.id == user_id))
            if user is None:
                return None
            coach = await session.get(MobileCoachProfileRecord, user_id)
            coach_profile = (
                CoachProfileSnapshot(
                    schema_version=coach.schema_version,
                    revision=coach.revision,
                    age_band=coach.age_band,
                    height_millimeters=coach.height_millimeters,
                    current_weight_grams=coach.current_weight_grams,
                    weight_measured_on=coach.weight_measured_on,
                    goal_type=coach.goal_type,
                    target_weight_grams=coach.target_weight_grams,
                    target_date=coach.target_date,
                    current_body_fat_basis_points=coach.current_body_fat_basis_points,
                    target_body_fat_basis_points=coach.target_body_fat_basis_points,
                    exercise_frequency=coach.exercise_frequency,
                    completed_at=coach.completed_at,
                    updated_at=coach.updated_at,
                )
                if coach is not None
                else None
            )
            return UserProfileSnapshot(
                nickname=user.nickname,
                first_seen_at=user.first_seen_at,
                coach_profile=coach_profile,
            )
