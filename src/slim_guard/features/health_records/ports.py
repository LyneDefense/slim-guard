"""Public domain ports; consumers do not depend on repository implementations."""

from typing import Protocol

from slim_guard.features.health_records.body_fat.contracts import (
    BodyFatMeasurementCommand,
    BodyFatRecordCreation,
    BodyFatRecordRef,
    BodyFatTrend,
)
from slim_guard.features.health_records.exercise.contracts import (
    ExerciseRecordCommand,
    ExerciseRecordCreation,
    ExerciseRecordRef,
)
from slim_guard.features.health_records.meal.contracts import (
    MealRecordCommand,
    MealRecordCreation,
    MealRecordRef,
)
from slim_guard.features.health_records.weight.contracts import (
    WeightMeasurementCommand,
    WeightRecordCreation,
    WeightRecordRef,
    WeightTrend,
)


class MealReader(Protocol):
    async def recent(self, user_id: str, *, limit: int = 10) -> tuple[MealRecordRef, ...]: ...


class MealStore(MealReader, Protocol):
    async def record(self, command: MealRecordCommand) -> MealRecordCreation: ...
    async def by_operation(self, user_id: str, operation_id: str) -> MealRecordRef | None: ...


class ExerciseReader(Protocol):
    async def recent(self, user_id: str, *, limit: int = 10) -> tuple[ExerciseRecordRef, ...]: ...


class ExerciseStore(ExerciseReader, Protocol):
    async def record(self, command: ExerciseRecordCommand) -> ExerciseRecordCreation: ...
    async def by_operation(self, user_id: str, operation_id: str) -> ExerciseRecordRef | None: ...


class WeightReader(Protocol):
    async def recent_trend(self, user_id: str, *, limit: int = 7) -> WeightTrend: ...


class WeightStore(WeightReader, Protocol):
    async def record(self, command: WeightMeasurementCommand) -> WeightRecordCreation: ...
    async def by_operation(self, user_id: str, operation_id: str) -> WeightRecordRef | None: ...


class BodyFatReader(Protocol):
    async def recent_trend(self, user_id: str, *, limit: int = 7) -> BodyFatTrend: ...


class BodyFatStore(BodyFatReader, Protocol):
    async def record(self, command: BodyFatMeasurementCommand) -> BodyFatRecordCreation: ...
    async def by_operation(self, user_id: str, operation_id: str) -> BodyFatRecordRef | None: ...
