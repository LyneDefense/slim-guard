from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION
from slim_guard.agent.runtime import AgentRuntime
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.db.session import Database
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.records.service import UserRecordStatusService
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.context import ContextCompiler
from slim_guard.harness.context_data import AuthoritativeContextDataProvider
from slim_guard.harness.initialization import TurnInitializer
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.pending_resume import PendingActionResumeCoordinator
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.runner import HarnessTurnRunner
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.memory.handoff import HandoffRepository
from slim_guard.memory.registry import MemorySchemaRegistry
from slim_guard.memory.repository import MEMORY_POLICY_VERSION, MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.exercise import exercise_tool_definitions, exercise_tool_executors
from slim_guard.tools.gateway import ToolGateway
from slim_guard.tools.image import image_tool_definitions, image_tool_executors
from slim_guard.tools.meal import meal_tool_definitions, meal_tool_executors
from slim_guard.tools.memory import memory_tool_definitions, memory_tool_executors
from slim_guard.tools.pending import (
    PendingActionToolHandlers,
    pending_action_tool_definitions,
    pending_action_tool_executors,
)
from slim_guard.tools.policy import DefaultToolPolicy
from slim_guard.tools.records import (
    record_status_tool_definitions,
    record_status_tool_executors,
)
from slim_guard.tools.registry import ToolRegistry
from slim_guard.tools.routine import routine_tool_definitions, routine_tool_executors
from slim_guard.tools.weight import weight_tool_definitions, weight_tool_executors


class AgentRuntimeDefinition(BaseModel):
    """Versioned runtime choices needed to construct one Agent graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_provider: str = Field(min_length=1, max_length=128)
    text_model: str = Field(min_length=1, max_length=256)
    vision_model: str = Field(min_length=1, max_length=256)
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    code_revision: str = Field(min_length=1, max_length=256)
    limits: HarnessLimits = Field(default_factory=HarnessLimits)
    confirmation_ttl_seconds: int = Field(default=900, ge=1, le=86_400)
    review_ttl_seconds: int = Field(default=86_400, ge=1, le=604_800)
    image_retention_seconds: int = Field(default=604_800, ge=3600, le=2_592_000)
    vision_max_output_tokens: int = Field(default=1024, ge=64, le=32_768)
    memory_preload_max_facts: int = Field(default=30, ge=1, le=100)
    memory_health_review_days: int = Field(default=180, ge=30, le=730)
    memory_recent_turn_count: int = Field(default=3, ge=1, le=10)
    memory_recent_dialogue_max_chars: int = Field(default=1500, ge=100, le=10_000)
    memory_handoff_ttl_days: int = Field(default=14, ge=1, le=90)


def build_agent_runtime(
    *,
    database: Database,
    model: ModelGateway,
    vision: VisionModelGateway | None = None,
    definition: AgentRuntimeDefinition,
    manifest: AgentManifest | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentRuntime:
    """Compose production repositories and gateways without channel dependencies."""

    tool_definitions = (
        *weight_tool_definitions(),
        *image_tool_definitions(),
        *meal_tool_definitions(),
        *exercise_tool_definitions(),
        *routine_tool_definitions(),
        *record_status_tool_definitions(),
        *memory_tool_definitions(),
        *pending_action_tool_definitions(),
    )
    registry = ToolRegistry(tool_definitions)
    expected_manifest = build_agent_manifest(definition)
    active_manifest = manifest or expected_manifest
    if active_manifest != expected_manifest:
        raise ValueError("Agent Runtime manifest does not match its definition")
    state = HarnessStateRepository(database)
    pending_actions = PendingActionRepository(database)
    pending_handlers = PendingActionToolHandlers(
        pending_actions=pending_actions,
        state=state,
        clock=clock,
    )
    assets = ImageAssetRepository(database)
    weights = WeightRepository(database)
    meals = MealRepository(database)
    exercise = ExerciseRepository(database)
    routines = RoutinePreferenceRepository(database)
    checkins = DailyCheckinStatusRepository(database)
    memories = MemoryRepository(
        database,
        registry=MemorySchemaRegistry(
            health_review_days=definition.memory_health_review_days,
        ),
        clock=clock,
    )
    conversation = ConversationWindowRepository(database)
    handoffs = HandoffRepository(
        database,
        ttl=timedelta(days=definition.memory_handoff_ttl_days),
        clock=clock,
    )
    executors = {
        **weight_tool_executors(
            weights,
            clock=clock,
        ),
        **image_tool_executors(
            assets=assets,
            vision=vision,
            vision_model=definition.vision_model,
            max_output_tokens=definition.vision_max_output_tokens,
            clock=clock,
        ),
        **meal_tool_executors(meals, clock=clock),
        **exercise_tool_executors(exercise, clock=clock),
        **routine_tool_executors(routines),
        **record_status_tool_executors(UserRecordStatusService(database)),
        **memory_tool_executors(memories, handoffs),
        **pending_action_tool_executors(pending_handlers),
    }
    gateway = ToolGateway(
        registry=registry,
        executors=executors,
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    tool_calls = ToolCallCoordinator(
        gateway=gateway,
        pending_actions=pending_actions,
        turn_state=state,
        confirmation_ttl=timedelta(seconds=definition.confirmation_ttl_seconds),
        review_ttl=timedelta(seconds=definition.review_ttl_seconds),
    )
    pending_handlers.bind(
        PendingActionResumeCoordinator(
            pending_actions=pending_actions,
            turn_state=state,
            tool_calls=tool_calls,
        )
    )
    recorder = PersistentHarnessRunRecorder(state)
    runner = HarnessTurnRunner(
        initializer=TurnInitializer(state),
        compiler=ContextCompiler(
            manifest=active_manifest,
            system_prompt=SLIM_GUARD_HARNESS_PROMPT,
            tools=registry,
        ),
        model=model,
        tool_calls=tool_calls,
        recorder=recorder,
        limits=definition.limits,
        context_data=AuthoritativeContextDataProvider(
            database=database,
            weights=weights,
            meals=meals,
            exercise=exercise,
            routines=routines,
            checkins=checkins,
            memories=memories,
            conversation=conversation,
            handoffs=handoffs,
            pending_actions=pending_actions,
            memory_limit=definition.memory_preload_max_facts,
            dialogue_turn_limit=definition.memory_recent_turn_count,
            dialogue_char_limit=definition.memory_recent_dialogue_max_chars,
        ),
        output_guard=SlimGuardOutputGuard(),
        clock=clock,
    )
    return AgentRuntime(
        manifest=active_manifest,
        versions=AgentVersionRepository(database),
        runner=runner,
        assets=assets,
        image_retention=timedelta(seconds=definition.image_retention_seconds),
        clock=clock,
    )


def build_agent_manifest(definition: AgentRuntimeDefinition) -> AgentManifest:
    registry = ToolRegistry(
        (
            *weight_tool_definitions(),
            *image_tool_definitions(),
            *meal_tool_definitions(),
            *exercise_tool_definitions(),
            *routine_tool_definitions(),
            *record_status_tool_definitions(),
            *memory_tool_definitions(),
            *pending_action_tool_definitions(),
        )
    )
    return AgentManifest.build(
        model_provider=definition.model_provider,
        text_model=definition.text_model,
        vision_model=definition.vision_model,
        model_parameters=definition.model_parameters,
        system_prompt_version=SLIM_GUARD_PROMPT_VERSION,
        system_prompt=SLIM_GUARD_HARNESS_PROMPT,
        tool_versions=registry.versions,
        context_policy_version="authoritative-working-memory-privacy-v5",
        memory_policy_version=MEMORY_POLICY_VERSION,
        compaction_policy_version="bounded-working-handoff-redaction-v2",
        safety_policy_version="health-output-guard-v2",
        code_revision=definition.code_revision,
    )
