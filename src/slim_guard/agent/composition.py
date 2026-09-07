from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION
from slim_guard.agent.runtime import AgentRuntime
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.agents.nutrition import (
    DEFAULT_NUTRITION_PROMPT_VERSION,
    NUTRITION_AGENT_ALLOWED_TOOLS,
    NUTRITION_AGENT_PROMPT,
)
from slim_guard.agents.nutrition.tools import NutritionToolRegistry
from slim_guard.agents.style import RESPONSE_STYLE_PROMPT, RESPONSE_STYLE_PROMPT_VERSION
from slim_guard.db.session import Database
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.domain.body_fat.repository import BodyFatRepository
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
from slim_guard.harness.manifest import AgentGraphManifest, AgentGraphNodeManifest, AgentManifest
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.pending_resume import PendingActionResumeCoordinator
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.runner import HarnessTurnRunner
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.tool_calls import ToolCallCoordinator
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.memory.engine import MemoryEngine
from slim_guard.memory.handoff import HandoffRepository
from slim_guard.memory.ingestion import ModelFirstMemoryIngestor
from slim_guard.memory.recall import ModelFirstMemoryRecaller
from slim_guard.memory.registry import MemorySchemaRegistry
from slim_guard.memory.repository import MEMORY_POLICY_VERSION, MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository
from slim_guard.nutrition_knowledge import (
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)
from slim_guard.orchestration.coordinator import (
    SHADOW_ORCHESTRATOR_PROMPT,
    SHADOW_ORCHESTRATOR_PROMPT_VERSION,
    AgentWorkflowCoordinator,
)
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.style_profiles import StyleProfileRepository
from slim_guard.tools.body_fat import body_fat_tool_definitions, body_fat_tool_executors
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
    memory_recent_image_count: int = Field(default=3, ge=1, le=10)
    memory_handoff_ttl_days: int = Field(default=14, ge=1, le=90)
    memory_ingestion_history_count: int = Field(default=20, ge=1, le=100)
    memory_ingestion_history_max_chars: int = Field(default=6000, ge=100, le=20_000)
    memory_recall_search_limit: int = Field(default=12, ge=1, le=100)
    memory_recall_max_selected: int = Field(default=8, ge=1, le=20)
    multi_agent_mode: Literal["off", "shadow"] = "off"
    multi_agent_graph_version: str = Field(
        default="typed-supervisor-v1",
        min_length=1,
        max_length=128,
    )
    multi_agent_shadow_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    default_style_profile: str = Field(
        default="slimguard_default_v1",
        min_length=1,
        max_length=128,
    )
    style_render_all_normal_replies: bool = True
    nutrition_agent_enabled: bool = False
    nutrition_rag_enabled: bool = False
    nutrition_require_rag_citations: bool = True

    @model_validator(mode="after")
    def validate_nutrition_rag(self) -> AgentRuntimeDefinition:
        if self.nutrition_rag_enabled and not self.nutrition_agent_enabled:
            raise ValueError("Nutrition RAG requires the Nutrition Agent")
        if self.nutrition_rag_enabled and not self.nutrition_require_rag_citations:
            raise ValueError("Nutrition RAG citations cannot be disabled")
        return self


def build_agent_runtime(
    *,
    database: Database,
    model: ModelGateway,
    memory_ingestion_model: ModelGateway | None = None,
    memory_recall_model: ModelGateway | None = None,
    memory_engine: MemoryEngine | None = None,
    vision: VisionModelGateway | None = None,
    definition: AgentRuntimeDefinition,
    manifest: AgentManifest | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentRuntime:
    """Compose production repositories and gateways without channel dependencies."""

    tool_definitions = (
        *weight_tool_definitions(),
        *body_fat_tool_definitions(),
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
    body_fat = BodyFatRepository(database)
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
        index_sync_enabled=memory_engine is not None,
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
        **body_fat_tool_executors(body_fat, clock=clock),
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
    memory_ingestor = (
        ModelFirstMemoryIngestor(
            model=memory_ingestion_model,
            model_name=definition.text_model,
            conversation=conversation,
            memories=memories,
            tool_calls=tool_calls,
            recorder=recorder,
            history_limit=definition.memory_ingestion_history_count,
            history_char_limit=definition.memory_ingestion_history_max_chars,
            max_output_tokens=definition.vision_max_output_tokens,
        )
        if memory_ingestion_model is not None
        else None
    )
    memory_recaller = (
        ModelFirstMemoryRecaller(
            model=memory_recall_model,
            model_name=definition.text_model,
            recorder=recorder,
            engine=memory_engine,
            search_limit=definition.memory_recall_search_limit,
            max_selected=definition.memory_recall_max_selected,
        )
        if memory_recall_model is not None
        else None
    )
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
            body_fat=body_fat,
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
            recent_image_limit=definition.memory_recent_image_count,
        ),
        memory_ingestor=memory_ingestor,
        memory_recaller=memory_recaller,
        output_guard=SlimGuardOutputGuard(),
        shadow_workflow=(
            AgentWorkflowCoordinator(
                model=model,
                recorder=recorder,
                model_name=definition.text_model,
                graph_version=definition.multi_agent_graph_version,
                timeout=timedelta(seconds=definition.multi_agent_shadow_timeout_seconds),
                max_output_tokens=definition.vision_max_output_tokens,
                persistence=OrchestrationRepository(database),
                style_profiles=StyleProfileRepository(database),
                style_enabled=definition.style_render_all_normal_replies,
                nutrition_enabled=definition.nutrition_agent_enabled,
                nutrition_tools=NutritionToolRegistry(
                    knowledge_repository=(
                        NutritionKnowledgeService(
                            NutritionKnowledgeRepository(database)
                        )
                        if definition.nutrition_rag_enabled
                        else None
                    )
                ),
                clock=clock,
            )
            if definition.multi_agent_mode == "shadow"
            else None
        ),
        shadow_enabled_for=lambda _user_id: definition.multi_agent_mode == "shadow",
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
            *body_fat_tool_definitions(),
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
        context_policy_version="model-ranked-authoritative-working-memory-receipt-v9",
        memory_policy_version=MEMORY_POLICY_VERSION,
        compaction_policy_version="bounded-working-images-handoff-redaction-v3",
        safety_policy_version="health-output-guard-v2",
        code_revision=definition.code_revision,
    )


def build_agent_graph_manifest(definition: AgentRuntimeDefinition) -> AgentGraphManifest:
    """Freeze active and planned roles for the typed workflow."""

    disabled_prompt = "This workflow role is disabled in the current rollout increment."
    nodes = {
        "orchestrator": AgentGraphNodeManifest.build(
            role="orchestrator",
            model=definition.text_model,
            prompt_version=SHADOW_ORCHESTRATOR_PROMPT_VERSION,
            prompt=SHADOW_ORCHESTRATOR_PROMPT,
            output_schema="TurnDirective",
            privacy_scopes=("current_user_message", "trusted_context"),
            max_model_calls=2,
            max_tool_calls=0,
            max_total_tokens=definition.vision_max_output_tokens * 2,
        ),
        "nutrition_expert": AgentGraphNodeManifest.build(
            role="nutrition_expert",
            model=definition.text_model,
            prompt_version=DEFAULT_NUTRITION_PROMPT_VERSION,
            prompt=NUTRITION_AGENT_PROMPT,
            output_schema="ProfessionalAssessment",
            allowed_tool_names=NUTRITION_AGENT_ALLOWED_TOOLS,
            privacy_scopes=("evidence_packet", "nutrition_observations"),
            max_model_calls=2,
            max_tool_calls=0,
            max_total_tokens=definition.vision_max_output_tokens * 2,
        ),
        "response_style": AgentGraphNodeManifest.build(
            role="response_style",
            model=definition.text_model,
            prompt_version=RESPONSE_STYLE_PROMPT_VERSION,
            prompt=RESPONSE_STYLE_PROMPT,
            output_schema="StyledResponse",
            privacy_scopes=("response_plan", "style_profile"),
            max_model_calls=2,
            max_tool_calls=0,
            max_total_tokens=definition.vision_max_output_tokens * 2,
        ),
        "response_reviewer": AgentGraphNodeManifest.build(
            role="response_reviewer",
            model=definition.text_model,
            prompt_version="disabled-v1",
            prompt=disabled_prompt,
            output_schema="ReviewerVerdict",
            max_model_calls=1,
            max_tool_calls=0,
            max_total_tokens=definition.vision_max_output_tokens,
        ),
    }
    return AgentGraphManifest.build(
        graph_version=definition.multi_agent_graph_version,
        nodes=nodes,
        style_profile_version=definition.default_style_profile,
        routing_policy_version="model-directed-code-validated-v1",
        evidence_policy_version="typed-provenance-v1",
        safety_policy_version="health-output-guard-v2",
        business_tool_versions=dict(build_agent_manifest(definition).tool_versions),
        nutrition_tool_versions=NutritionToolRegistry().versions,
        code_revision=definition.code_revision,
    )
