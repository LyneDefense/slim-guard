from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from slim_guard.agent.assembly.attachments import build_attachment_components
from slim_guard.agent.assembly.business import build_business_components
from slim_guard.agent.assembly.memory import build_memory_components
from slim_guard.agent.assembly.nutrition import build_nutrition_components
from slim_guard.agent.assembly.responses import build_response_finalizer
from slim_guard.agent.assembly.tasks import build_task_components
from slim_guard.agent.assembly.tools import build_tool_calls
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.manifest import build_agent_manifest
from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT
from slim_guard.agent.runtime import AgentRuntime
from slim_guard.agent.tool_catalog import build_core_tool_registry
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agent_models.vision import VisionModelGateway
from slim_guard.agents.nutrition.tools import (
    NutritionKnowledgeRepository as NutritionKnowledgePort,
)
from slim_guard.db.session import Database
from slim_guard.harness.context import ContextCompiler
from slim_guard.harness.initialization import TurnInitializer
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.memory.engine import MemoryEngine
from slim_guard.orchestration.repository import OrchestrationRepository
from slim_guard.runtime.turn import TurnHarness
from slim_guard.usage.gateway import MeasuredModelGateway, MeasuredVisionGateway
from slim_guard.usage.repository import UsageRepository

# Existing Invocation/Trace label, not a selector or a deployment setting.
# Code revisions remain recorded by AgentManifest.
_CORE_PRIMARY_GRAPH_VERSION = "core-primary-v1"


def build_agent_runtime(
    *,
    database: Database,
    model: ModelGateway,
    memory_extraction_model: ModelGateway | None = None,
    memory_recall_model: ModelGateway | None = None,
    memory_engine: MemoryEngine | None = None,
    vision: VisionModelGateway | None = None,
    nutrition_knowledge: NutritionKnowledgePort | None = None,
    definition: AgentRuntimeDefinition,
    manifest: AgentManifest | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentRuntime:
    """Connect capability builders to the Turn Harness and application runtime."""

    registry = build_core_tool_registry()
    usage = UsageRepository(database)
    model = MeasuredModelGateway(model, usage, definition.models.provider)
    if vision is not None:
        vision = MeasuredVisionGateway(vision, usage, definition.models.provider)
    if memory_recall_model is not None:
        memory_recall_model = MeasuredModelGateway(
            memory_recall_model, usage, definition.models.provider
        )
    expected_manifest = build_agent_manifest(definition, registry=registry)
    active_manifest = manifest or expected_manifest
    if active_manifest != expected_manifest:
        raise ValueError("Agent Runtime manifest does not match its definition")

    state = HarnessStateRepository(database)
    tasks = build_task_components(database=database, config=definition.tasks, clock=clock)
    recorder = PersistentHarnessRunRecorder(state)
    invocation_store = OrchestrationRepository(database)
    memory = build_memory_components(
        database=database,
        config=definition.memory,
        model_name=definition.models.text_model,
        extraction_model=memory_extraction_model,
        recall_model=memory_recall_model,
        engine=memory_engine,
        recorder=recorder,
        clock=clock,
    )
    attachments = build_attachment_components(
        database=database,
        models=definition.models,
        vision=vision,
        clock=clock,
    )
    business = build_business_components(
        database=database,
        memory_config=definition.memory,
        memory=memory,
        tasks=tasks.store,
        pending_actions=tasks.authorizations,
        clock=clock,
    )
    nutrition = build_nutrition_components(
        model=model,
        model_name=definition.models.text_model,
        knowledge=nutrition_knowledge,
        state=state,
        context=business.context,
        invocation_store=invocation_store,
        graph_version=_CORE_PRIMARY_GRAPH_VERSION,
        timeout_seconds=definition.agent_specialist_timeout_seconds,
        clock=clock,
    )
    tool_calls = build_tool_calls(
        database=database,
        registry=registry,
        state=state,
        pending_actions=tasks.authorizations,
        business_executors={**business.executors, **attachments.executors},
        memory_executors=memory.executors,
        task_executors=tasks.executors,
        specialist_executors=nutrition.executors,
        config=definition.pending_actions,
        clock=clock,
    )
    response_finalizer = build_response_finalizer(
        database=database,
        model=model,
        model_name=definition.models.text_model,
        nutrition_specialist=nutrition.specialist,
        invocation_store=invocation_store,
        recorder=recorder,
        graph_version=_CORE_PRIMARY_GRAPH_VERSION,
        clock=clock,
    )
    runner = TurnHarness(
        initializer=TurnInitializer(state),
        compiler=ContextCompiler(
            manifest=active_manifest,
            system_prompt=SLIM_GUARD_HARNESS_PROMPT,
            tools=registry,
        ),
        model=model,
        tool_calls=tool_calls,
        recorder=recorder,
        context_data=business.context,
        memory_recaller=memory.recaller,
        output_guard=SlimGuardOutputGuard(),
        response_finalizer=response_finalizer,
        invocation_store=invocation_store,
        graph_version=_CORE_PRIMARY_GRAPH_VERSION,
        clock=clock,
    )
    return AgentRuntime(
        manifest=active_manifest,
        versions=AgentVersionRepository(database),
        runner=runner,
        assets=attachments.assets,
        memory_extraction_scheduler=memory.extraction_jobs,
        clock=clock,
    )
