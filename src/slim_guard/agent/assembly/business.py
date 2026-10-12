"""Share domain repositories between the context provider and business tools."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from slim_guard.agent.assembly.memory import MemoryComponents
from slim_guard.db.session import Database
from slim_guard.domain.profile.repository import UserProfileRepository
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.features.health_records.body_fat.repository import BodyFatRepository
from slim_guard.features.health_records.exercise.repository import ExerciseRepository
from slim_guard.features.health_records.meal.repository import MealRepository
from slim_guard.features.health_records.tools import health_record_tool_executors
from slim_guard.features.health_records.weight.repository import WeightRepository
from slim_guard.harness.context_data import AuthoritativeContextDataProvider, ContextDataProvider
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.services.record_changes import build_record_service
from slim_guard.tasks.ports import TaskStore
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.routine import routine_tool_executors


@dataclass(frozen=True, slots=True)
class BusinessComponents:
    context: ContextDataProvider
    executors: Mapping[str, ToolExecutor[Any]]


def build_business_components(
    *,
    database: Database,
    memory_config: MemoryRuntimeConfig,
    memory: MemoryComponents,
    tasks: TaskStore,
    clock: Callable[[], datetime] | None,
) -> BusinessComponents:
    weights = WeightRepository(database)
    body_fat = BodyFatRepository(database)
    meals = MealRepository(database)
    exercise = ExerciseRepository(database)
    routines = RoutinePreferenceRepository(database)
    checkins = DailyCheckinStatusRepository(database)
    records = build_record_service(database)
    context = AuthoritativeContextDataProvider(
        profiles=UserProfileRepository(database),
        records=records,
        weights=weights,
        body_fat=body_fat,
        meals=meals,
        exercise=exercise,
        routines=routines,
        checkins=checkins,
        memories=memory.profiles,
        conversation=memory.conversation,
        tasks=tasks,
        memory_limit=memory_config.preload_max_facts,
        dialogue_turn_limit=memory_config.recent_turn_count,
        dialogue_char_limit=memory_config.recent_dialogue_max_chars,
        preload_domain_history=False,
    )
    return BusinessComponents(
        context=context,
        executors={
            **health_record_tool_executors(records),
            **routine_tool_executors(routines),
        },
    )
