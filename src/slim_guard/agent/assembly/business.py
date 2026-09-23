"""Share domain repositories between the context provider and business tools."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from slim_guard.agent.assembly.memory import MemoryComponents
from slim_guard.db.session import Database
from slim_guard.domain.body_fat.repository import BodyFatRepository
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.records.service import UserRecordStatusService
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.context_data import AuthoritativeContextDataProvider, ContextDataProvider
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.tasks.ports import TaskStore
from slim_guard.tools.body_fat import body_fat_tool_executors
from slim_guard.tools.exercise import exercise_tool_executors
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.meal import meal_tool_executors
from slim_guard.tools.records import record_status_tool_executors
from slim_guard.tools.routine import routine_tool_executors
from slim_guard.tools.weight import weight_tool_executors


@dataclass(frozen=True, slots=True)
class BusinessComponents:
    context: ContextDataProvider
    executors: Mapping[str, ToolExecutor[Any]]


def build_business_components(
    *,
    database: Database,
    memory_config: MemoryRuntimeConfig,
    memory: MemoryComponents,
    pending_actions: PendingActionRepository,
    tasks: TaskStore,
    clock: Callable[[], datetime] | None,
) -> BusinessComponents:
    weights = WeightRepository(database)
    body_fat = BodyFatRepository(database)
    meals = MealRepository(database)
    exercise = ExerciseRepository(database)
    routines = RoutinePreferenceRepository(database)
    checkins = DailyCheckinStatusRepository(database)
    context = AuthoritativeContextDataProvider(
        database=database,
        weights=weights,
        body_fat=body_fat,
        meals=meals,
        exercise=exercise,
        routines=routines,
        checkins=checkins,
        memories=memory.profiles,
        conversation=memory.conversation,
        tasks=tasks,
        pending_actions=pending_actions,
        memory_limit=memory_config.preload_max_facts,
        dialogue_turn_limit=memory_config.recent_turn_count,
        dialogue_char_limit=memory_config.recent_dialogue_max_chars,
        preload_domain_history=False,
    )
    return BusinessComponents(
        context=context,
        executors={
            **weight_tool_executors(weights, clock=clock),
            **body_fat_tool_executors(body_fat, clock=clock),
            **meal_tool_executors(meals, clock=clock),
            **exercise_tool_executors(exercise, clock=clock),
            **routine_tool_executors(routines),
            **record_status_tool_executors(UserRecordStatusService(database)),
        },
    )
