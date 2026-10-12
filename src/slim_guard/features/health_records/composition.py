"""Public composition entry; concrete storage remains inside the feature."""

from dataclasses import dataclass

from slim_guard.db.session import Database
from slim_guard.features.health_records.body_fat.repository import BodyFatRepository
from slim_guard.features.health_records.exercise.repository import ExerciseRepository
from slim_guard.features.health_records.meal.repository import MealRepository
from slim_guard.features.health_records.ports import (
    BodyFatReader,
    ExerciseReader,
    MealReader,
    WeightReader,
)
from slim_guard.features.health_records.weight.repository import WeightRepository


@dataclass(frozen=True, slots=True)
class HealthRecordReaders:
    weights: WeightReader
    body_fat: BodyFatReader
    meals: MealReader
    exercise: ExerciseReader


def build_record_readers(database: Database) -> HealthRecordReaders:
    return HealthRecordReaders(
        weights=WeightRepository(database),
        body_fat=BodyFatRepository(database),
        meals=MealRepository(database),
        exercise=ExerciseRepository(database),
    )
